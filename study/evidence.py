"""Durable per-attempt evidence; recording never invokes a model or domain tool."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Callable
from uuid import uuid4

SCHEMA_VERSION = 1
ROOT_HASH = '0' * 64
PRIVATE_FIELDS = {'authorization', 'api_key', 'openai_api_key', 'access_token',
                  'refresh_token', 'cookie', 'set-cookie', 'headers', 'environment'}


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', value):
        raise ValueError('Invalid artifact identity')
    return value


def encoded(value) -> bytes:
    """No implicit repr conversion, NaN, or credential-bearing field containers.

    This is a boundary check, not a general secret scanner. Callers must supply
    only approved benchmark evidence, never raw environment/client objects.
    """
    def inspect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or key.lower() in PRIVATE_FIELDS:
                    raise ValueError('Disallowed evidence field')
                inspect(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                inspect(child)
    inspect(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


@dataclass(frozen=True)
class EpisodeKey:
    run_id: str
    domain: str
    split: str
    task_index: int
    trial: int
    arm: str
    benchmark_sha: str

    def __post_init__(self):
        safe_name(self.run_id)
        if self.domain not in {'retail', 'airline'} or self.arm not in {'B', 'G'}:
            raise ValueError('Invalid domain or experimental arm')
        if self.split not in ({'train', 'dev', 'test'} if self.domain == 'retail' else {'test'}):
            raise ValueError('Invalid domain split')
        if any(type(v) is not int or v < 0 for v in (self.task_index, self.trial)):
            raise ValueError('Task and trial identities must be nonnegative integers')
        if not re.fullmatch('[a-f0-9]{40}', self.benchmark_sha):
            raise ValueError('A full benchmark commit SHA is required')

    @property
    def cell_id(self):
        return sha256(encoded(asdict(self))).hexdigest()


def sync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def exclusive_json(path: Path, value):
    data = encoded(value) + b'\n'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


class AttemptJournal:
    """One writer per newly created attempt; interrupted attempts are immutable.

    A technical rerun creates another attempt ID in the same cell. This class
    deliberately does not resume or repeat a previously attempted mutation.
    """
    def __init__(self, root: Path, key: EpisodeKey, *, attempt_id: str | None = None,
                 clock: Callable[[], int] = time.monotonic_ns):
        self.key = key
        self.attempt_id = safe_name(attempt_id or uuid4().hex)
        self.directory = Path(root).resolve() / safe_name(key.run_id) / key.cell_id / self.attempt_id
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        for directory in (self.directory, *self.directory.parents):
            sync_directory(directory)
            if directory == Path(root).resolve():
                break
        self.clock = clock
        self._lock = threading.Lock()
        self._seq = 0
        self._last_hash = ROOT_HASH
        self._sealed = False
        exclusive_json(self.directory/'attempt.json', {
            'schema_version': SCHEMA_VERSION, 'episode': asdict(key),
            'cell_id': key.cell_id, 'attempt_id': self.attempt_id})
        descriptor = os.open(self.directory/'events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        file_stat = os.fstat(descriptor)
        self._file_identity = (file_stat.st_dev, file_stat.st_ino)
        self._file_size = 0
        os.fsync(descriptor)
        os.close(descriptor)
        sync_directory(self.directory)
        self.append('episode_started', {})

    def append(self, event_type: str, payload: dict) -> dict:
        safe_name(event_type)
        if not isinstance(payload, dict):
            raise ValueError('Event payload must be a dictionary')
        with self._lock:
            if self._sealed:
                raise RuntimeError('Attempt is sealed')
            event = {'schema_version': SCHEMA_VERSION, 'cell_id': self.key.cell_id,
                     'attempt_id': self.attempt_id, 'sequence': self._seq,
                     'monotonic_ns': self.clock(), 'event_type': event_type,
                     'payload': payload, 'previous_hash': self._last_hash}
            event_hash = sha256(encoded(event)).hexdigest()
            record = {**event, 'event_hash': event_hash}
            data = encoded(record) + b'\n'
            # Return only after the event is durable. A gate calls this BEFORE
            # invoking a native write; I/O failure must block that invocation.
            try:
                descriptor = os.open(self.directory/'events.jsonl', os.O_WRONLY | os.O_APPEND)
                with os.fdopen(descriptor, 'ab', buffering=0) as stream:
                    file_stat = os.fstat(stream.fileno())
                    if ((file_stat.st_dev, file_stat.st_ino) != self._file_identity or
                            file_stat.st_size != self._file_size):
                        raise OSError('Evidence file was replaced or changed; cannot continue')
                    if stream.write(data) != len(data):
                        raise OSError('Incomplete evidence write; attempt cannot continue')
                    os.fsync(stream.fileno())
            except BaseException:
                self._sealed = True
                raise
            self._seq += 1
            self._last_hash = event_hash
            self._file_size += len(data)
            return record

    def seal(self, *, status: str, last_native_reward: float | None, native_done: bool,
             details: dict | None = None):
        allowed = {'native_done', 'native_action_budget', 'controller_budget',
                   'deadline', 'model_error', 'tool_error', 'uncertain_mutation', 'cancelled'}
        if status not in allowed or type(native_done) is not bool:
            raise ValueError('Invalid final status')
        if last_native_reward not in (None, 0, 1) or isinstance(last_native_reward, bool):
            raise ValueError('Native reward must remain zero, one, or missing')
        if status == 'native_done' and not native_done:
            raise ValueError('Cannot invent native termination')
        if native_done and status != 'native_done':
            raise ValueError('Terminal native outcome must be identified explicitly')
        if not native_done and last_native_reward == 1:
            raise ValueError('Cannot invent successful nonterminal native reward')
        with self._lock:
            if self._sealed:
                raise RuntimeError('Attempt is already sealed')
            result = {'schema_version': SCHEMA_VERSION, 'episode': asdict(self.key),
                      'cell_id': self.key.cell_id, 'attempt_id': self.attempt_id,
                      'status': status, 'last_native_reward': last_native_reward,
                      'native_done': native_done, 'event_count': self._seq,
                      'final_event_hash': self._last_hash, 'details': details or {}}
            try:
                exclusive_json(self.directory/'outcome.json', result)
            finally:
                self._sealed = True
            return result


def read_events(path: Path) -> list[dict]:
    """Read-only chain validation; never repair/truncate or execute event data."""
    data = Path(path).read_bytes()
    if data and not data.endswith(b'\n'):
        raise ValueError('Incomplete last event; preserve interrupted attempt')
    previous, identity, records = ROOT_HASH, None, []
    for sequence, line in enumerate(data.splitlines()):
        record = json.loads(line)
        event_hash = record.pop('event_hash')
        current_identity = record.get('cell_id'), record.get('attempt_id')
        if identity is None:
            identity = current_identity
        if (record.get('schema_version') != SCHEMA_VERSION or record.get('sequence') != sequence
                or record.get('previous_hash') != previous or current_identity != identity
                or sha256(encoded(record)).hexdigest() != event_hash):
            raise ValueError('Evidence identity, order, or hash mismatch')
        records.append({**record, 'event_hash': event_hash})
        previous = event_hash
    return records


def inventory(root: Path, run_id: str) -> list[dict]:
    """List every attempt, including unsealed interruptions, without choosing winners."""
    result = []
    for path in sorted((Path(root)/safe_name(run_id)).glob('*/*/attempt.json')):
        identity = json.loads(path.read_text())
        key = EpisodeKey(**identity['episode'])
        if (key.cell_id != identity['cell_id'] or key.run_id != run_id or
                path.parent.name != identity['attempt_id'] or
                path.parent.parent.name != key.cell_id):
            raise ValueError('Attempt manifest does not match canonical episode or directory identity')
        events = read_events(path.parent/'events.jsonl')
        outcome_path = path.parent/'outcome.json'
        outcome = json.loads(outcome_path.read_text()) if outcome_path.exists() else None
        if any((event['cell_id'], event['attempt_id']) != (identity['cell_id'], identity['attempt_id'])
               for event in events):
            raise ValueError('Attempt manifest does not match event identity')
        if outcome and (outcome['episode'] != identity['episode'] or
                        outcome['cell_id'] != identity['cell_id'] or
                        outcome['attempt_id'] != identity['attempt_id']):
            raise ValueError('Outcome belongs to a different attempt')
        if outcome and (outcome['event_count'] != len(events) or
                        outcome['final_event_hash'] != (events[-1]['event_hash'] if events else ROOT_HASH)):
            raise ValueError('Outcome does not seal the current event chain')
        result.append({'identity': identity, 'event_count': len(events),
                       'status': outcome['status'] if outcome else 'interrupted_unsealed',
                       'outcome': outcome})
    return result
