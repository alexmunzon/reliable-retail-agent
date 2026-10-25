"""Validated, finite service configuration read from RRA_* environment variables."""
from __future__ import annotations

from dataclasses import dataclass, fields
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "runtime" / "serving"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DEFAULT_DATA_DIR
    provider_url: str = "http://127.0.0.1:8766"
    test_mode: bool = False
    test_failpoints: str = ""
    # Request and storage limits.
    max_body_bytes: int = 16_384
    max_event_bytes: int = 16_384
    max_sessions_per_principal: int = 32
    max_active_runs_per_principal: int = 8
    max_pending_operations: int = 256
    # Rate limits (in-memory, per process; reset on restart).
    command_rate_per_minute: int = 60
    command_burst: int = 20
    unauthenticated_rate_per_minute: int = 30
    # Streaming.
    max_streams_per_principal: int = 4
    max_streams_global: int = 64
    sse_batch_size: int = 50
    sse_heartbeat_seconds: float = 15.0
    sse_send_timeout_seconds: float = 10.0
    sse_max_stream_seconds: float = 300.0
    # Worker and provider.
    worker_concurrency: int = 4
    worker_scan_seconds: float = 1.0
    provider_timeout_seconds: float = 5.0
    drain_seconds: float = 5.0
    # Recording a provider outcome: transient "busy/locked" errors are retried for at most
    # completion_retry_seconds; then (or at once for any other error) the outcome is parked off
    # the dispatch slot, dispatch pauses, and a write probe runs every recovery_probe_seconds.
    completion_retry_seconds: float = 10.0
    recovery_probe_seconds: float = 15.0
    reconcile_max_attempts: int = 20
    reconcile_backoff_seconds: float = 1.0
    reconcile_backoff_max_seconds: float = 60.0
    # Lifetimes and retention.
    session_ttl_seconds: int = 86_400
    proposal_ttl_seconds: int = 900
    authorization_ttl_seconds: int = 300
    idempotency_ttl_seconds: int = 86_400
    event_retention_seconds: int = 7 * 86_400
    maintenance_seconds: float = 60.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "serving.db"

    @property
    def credentials_path(self) -> Path:
        return self.data_dir / "credentials.json"

    @property
    def provider_key_path(self) -> Path:
        return self.data_dir / "provider.key"

    def provider_key(self) -> str:
        return self.provider_key_path.read_text(encoding="utf-8").strip()

    def failpoint(self, name: str) -> bool:
        """Crash hooks exist only in explicit synthetic test mode."""
        return self.test_mode and name in {part.strip() for part in self.test_failpoints.split(",")}


def _parse(kind: type, raw: str, name: str):
    if kind is bool:
        if raw not in {"0", "1"}:
            raise ValueError(f"{name} must be 0 or 1")
        return raw == "1"
    if kind is int:
        value = int(raw)
        if value < 1:
            raise ValueError(f"{name} must be a positive integer")
        return value
    if kind is float:
        value = float(raw)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number")
        return value
    if kind is Path:
        return Path(raw).resolve()
    return raw


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    environ = os.environ if environ is None else environ
    values = {}
    for item in fields(Settings):
        name = "RRA_" + item.name.upper()
        if name in environ:
            kind = {"bool": bool, "int": int, "float": float, "Path": Path}.get(str(item.type), str)
            values[item.name] = _parse(kind, environ[name], name)
    settings = Settings(**values)
    try:
        url = urlsplit(settings.provider_url)
        loopback = (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"}
                    and url.username is None and url.password is None and url.port is not None and url.port > 0
                    and url.path in {"", "/"} and not url.query and not url.fragment
                    and not any(character.isspace() or ord(character) < 32 or ord(character) == 127
                                for character in settings.provider_url))
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError("The local pilot only talks to a loopback synthetic provider")
    if settings.test_failpoints and not settings.test_mode:
        raise ValueError("RRA_TEST_FAILPOINTS requires RRA_TEST_MODE=1")
    return settings
