#!/usr/bin/env python3
"""Local-pilot operator commands for the serving backend (requires requirements-serving.lock).

  init            create/migrate the DB, seed synthetic principals, issue tokens, create the provider key
  migrate         apply pending schema migrations (refuses altered or newer schemas)
  rotate ACTOR    issue a new token (old one stops working immediately)
  revoke ACTOR    revoke a principal (open streams close at their next credential check)
  backup PATH     consistent online snapshot via SQLite's backup API
  restore PATH    replace the DB with a snapshot; service starts read-only until verify-restore
  verify-restore  settle open operations and revisions from provider evidence, then re-enable writes
  serve           run provider + app together on loopback (Ctrl-C stops both gracefully)
  serve-app / serve-provider   run one process (used by tests and the demo)
  arm ACTION...   queue synthetic provider failures (provider must run with RRA_TEST_MODE=1)
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
from hashlib import file_digest
from uuid import uuid4
import os
from contextlib import contextmanager
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from serving import auth, storage  # noqa: E402
from serving.config import load_settings  # noqa: E402


def hold_instance_lock(settings):
    """One process per data dir: serve-app, init/migrate, restore and verify-restore each need it exclusively.

    The OS releases the lock when the process exits (also on a crash). The descriptor is returned and must
    stay referenced for the lock's lifetime. backup (online API), rotate and revoke stay usable while serving.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / "serving.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        print(f"Refusing: another serving process holds {path} (one service process per data directory).",
              file=sys.stderr)
        raise SystemExit(3) from None
    return fd


@contextmanager
def credential_lock(settings):
    """Short exclusive lock shared by rotate/revoke and restore: a credential change is never written
    into a database file that restore is about to replace, and restore never misses one."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(settings.data_dir / "credentials.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def cmd_init(settings, args):
    storage.migrate(settings.db_path)
    conn = storage.connect(settings.db_path)
    try:
        with storage.write_tx(conn):
            issued = auth.seed(conn)
    finally:
        conn.close()
    if issued:
        auth.write_credentials(settings.credentials_path, issued)
    if not settings.provider_key_path.exists():
        fd = os.open(settings.provider_key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_urlsafe(32))
    print(f"Initialized {settings.db_path} (schema v{storage.SCHEMA_VERSION}).")
    print(f"Tokens for {sorted(issued) or 'no new principals'} written to {settings.credentials_path} (mode 0600, gitignored).")


def cmd_migrate(settings, args):
    print(f"Schema version {storage.migrate(settings.db_path)}")


def _change_credential(settings, actor, rotate):
    with credential_lock(settings):
        conn = storage.connect(settings.db_path)
        try:
            with storage.write_tx(conn):
                token = auth.rotate(conn, actor) if rotate else auth.revoke(conn, actor)
        finally:
            conn.close()
        if rotate:
            auth.write_credentials(settings.credentials_path, {actor: token})
    if rotate:
        print(f"Rotated {actor}; new token stored in {settings.credentials_path}.")
    else:
        print(f"Revoked {actor}.")


def cmd_backup(settings, args):
    storage.backup(settings.db_path, Path(args.path))
    print(f"Backup written to {args.path}. Treat it as sensitive: it contains synthetic consent payloads.")


def cmd_restore(settings, args):
    """Run only while the service is stopped. A crash at any step leaves either the old live DB or a
    snapshot already marked `unverified`: the marker is written into the staged copy before the
    atomic rename, so a restored snapshot can never start with dispatch enabled."""
    source = Path(args.path)
    storage.check_schema(source)
    with credential_lock(settings):
        _restore_locked(settings, source)


def _restore_locked(settings, source):
    staged = settings.db_path.with_name("serving.restore-staging.db")
    staged.unlink(missing_ok=True)
    storage.backup(source, staged)
    # Hash the consistent SQLite backup, including committed source WAL content.
    with staged.open("rb") as snapshot:
        snapshot_sha256 = file_digest(snapshot, "sha256").hexdigest()
    generation = uuid4().hex  # transport/restore identity, never an authentication credential
    conn = storage.connect(staged)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        with storage.write_tx(conn):
            storage.set_meta(conn, "restore_state", "unverified")
            storage.set_meta(conn, "restore_generation", generation)
            storage.set_meta(conn, "restore_snapshot_sha256", snapshot_sha256)
            storage.set_meta(conn, "restore_source", source.name)
            # Credentials are not history: keep today's revocations and rotations, never the backup's.
            carried = _carry_principals(settings.db_path, conn) if settings.db_path.exists() else 0
    finally:
        conn.close()
    if settings.db_path.exists():
        aside = settings.db_path.with_name(f"serving.pre-restore-{int(time.time())}-{generation}.db")
        storage.backup(settings.db_path, aside)
        print(f"Current database preserved at {aside}.")
        live = storage.connect(settings.db_path)
        try:  # Fold the live WAL into the main file so no stale WAL can attach to the new file.
            live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            live.execute("PRAGMA journal_mode=DELETE")
        finally:
            live.close()
    os.replace(staged, settings.db_path)
    print(f"Restored ({carried} current credentials kept). Writes and dispatch stay disabled until "
          "`verify-restore` succeeds.")


def _carry_principals(live_path, staged_conn) -> int:
    live = storage.connect(live_path)
    try:
        rows = live.execute("SELECT * FROM principals").fetchall()
    finally:
        live.close()
    for row in rows:  # Update in place: other tables reference principals by actor_id.
        updated = staged_conn.execute("UPDATE principals SET tenant_id=?, token_hash=?, scopes=?, status=?, rotated_at=? "
                                      "WHERE actor_id=?", (row["tenant_id"], row["token_hash"], row["scopes"],
                                                           row["status"], row["rotated_at"], row["actor_id"])).rowcount
        if not updated:
            staged_conn.execute("INSERT INTO principals VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(row))
    return len(rows)


async def _verify_restore(settings):
    from serving.provider import ProviderClient
    from serving.service import Service
    service = Service(settings)
    provider = ProviderClient(settings.provider_url, settings.provider_key(), settings.provider_timeout_seconds)
    try:
        # Operations created after the snapshot are absent from it. Safety then rests on the provider:
        # its fenced status lookup and revision preconditions. A provider without them cannot verify.
        probe = await provider.status("rra-restore-probe-" + secrets.token_hex(8))
        if probe.state != "failed_no_effect":
            print(f"Provider cannot give authoritative status evidence ({probe.code}); the database stays read-only.")
            return 1
        for operation_id, key, state in service.restore_open_operations():
            outcome = await provider.status(key)
            result = service.restore_resolve(operation_id, outcome)
            print(f"{operation_id} ({state}): {result} [{outcome.code}]")
            if result == "unresolved":
                print("Provider cannot establish this operation's outcome; the database stays read-only.")
                return 1
        for operation_id, key in service.restore_awaiting_proposals():
            outcome = await provider.status(key)  # Also fences a key that was never sent.
            result = service.restore_resolve_proposal(operation_id, outcome)
            print(f"{operation_id} (awaiting_consent): {result} [{outcome.code}]")
            if result == "unresolved":
                print("Provider cannot establish whether this prompt was acted on; the database stays read-only.")
                return 1
        revisions = {}
        for tenant_id, resource_id in service.restore_resources():
            order = await provider.get_order(tenant_id, resource_id)
            if order is not None:
                revisions[(tenant_id, resource_id)] = order["revision"]
        service.restore_complete(revisions)
        print("Restore verified against provider evidence; writes re-enabled.")
        return 0
    finally:
        await provider.aclose()


def cmd_serve_app(settings, args):
    import uvicorn
    from serving.app import create_app
    storage.check_schema(settings.db_path)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, access_log=False, log_level="warning",
                timeout_graceful_shutdown=int(settings.drain_seconds) + 1, lifespan="on")


def provider_command(settings, port, mode, test_mode):
    command = [sys.executable, "-m", "provider_sim.server", "--data-dir", str(settings.data_dir.parent / "provider"),
               "--key-file", str(settings.provider_key_path), "--port", str(port), "--mode", mode]
    return command + (["--test-mode"] if test_mode else [])


def cmd_serve(settings, args):
    provider_port = int(settings.provider_url.rsplit(":", 1)[1])
    provider = subprocess.Popen(provider_command(settings, provider_port, args.provider_mode, settings.test_mode), cwd=ROOT,
                                stdout=subprocess.PIPE, text=True)
    # Never fall through to some other process already bound to the provider port (it could be a
    # test-mode provider with failures armed): start the app only after OUR provider is listening.
    ready = provider.stdout.readline()
    if not ready.startswith("PROVIDER_READY"):
        provider.kill()
        print("The synthetic provider did not start (is its port already in use?); nothing was started.")
        return 1
    threading.Thread(target=shutil.copyfileobj, args=(provider.stdout, sys.stdout), daemon=True).start()
    app = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve-app", "--port", str(args.port)], cwd=ROOT)
    print(f"Synthetic provider ({args.provider_mode} mode) on 127.0.0.1:{provider_port}; "
          f"API and demo client on http://127.0.0.1:{args.port}/serving/", flush=True)
    try:
        app.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for process in (app, provider):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in (app, provider):
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def cmd_arm(settings, args):
    """Demo walkthrough helper: the provider accepts this only when started with --test-mode."""
    import httpx
    if not settings.test_mode:
        print("Refusing: failure injection needs RRA_TEST_MODE=1 in this shell too.")
        return 1
    response = httpx.post(f"{settings.provider_url}/v1/test/failpoints", json={"actions": args.actions},
                          headers={"Authorization": f"Bearer {settings.provider_key()}"}, timeout=5, trust_env=False)
    if response.status_code != 200:
        print("Provider refused failure injection (start `serve` with RRA_TEST_MODE=1).")
        return 1
    print(f"Armed, in order: {response.json()['armed']}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("migrate")
    for name in ("rotate", "revoke"):
        sub.add_parser(name).add_argument("actor")
    for name in ("backup", "restore"):
        sub.add_parser(name).add_argument("path")
    sub.add_parser("verify-restore")
    serve_app = sub.add_parser("serve-app")
    serve_app.add_argument("--port", type=int, default=8780)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8780)
    serve.add_argument("--provider-mode", choices=("full", "basic"), default="full")
    serve_provider = sub.add_parser("serve-provider")
    serve_provider.add_argument("--mode", choices=("full", "basic"), default="full")
    sub.add_parser("arm").add_argument("actions", nargs="+")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command in {"init", "migrate", "restore", "verify-restore", "serve-app"}:
        lock = hold_instance_lock(settings)  # noqa: F841 - held until this process exits.
    if args.command == "init":
        return cmd_init(settings, args)
    if args.command == "migrate":
        return cmd_migrate(settings, args)
    if args.command in {"rotate", "revoke"}:
        return _change_credential(settings, args.actor, args.command == "rotate")
    if args.command == "backup":
        return cmd_backup(settings, args)
    if args.command == "restore":
        return cmd_restore(settings, args)
    if args.command == "verify-restore":
        return asyncio.run(_verify_restore(settings))
    if args.command == "serve-app":
        return cmd_serve_app(settings, args)
    if args.command == "serve-provider":
        port = int(settings.provider_url.rsplit(":", 1)[1])
        os.execv(sys.executable, provider_command(settings, port, args.mode, settings.test_mode))
    if args.command == "serve":
        return cmd_serve(settings, args)
    if args.command == "arm":
        return cmd_arm(settings, args)


if __name__ == "__main__":
    raise SystemExit(main())
