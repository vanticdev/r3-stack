"""Preserve an MCP subprocess's stdio while retaining private failure evidence.

The protocol streams are inherited unchanged. Never restart a failed child or
replay a request: a disconnected mutation may have an unknown outcome.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

STDERR_LIMIT = 1024 * 1024


def run(command: list[str], directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    state = {"started_at": datetime.now(timezone.utc).isoformat(),
             "wrapper_pid": os.getpid(), "command": Path(command[0]).name,
             "stderr_bytes": 0, "stderr_truncated": False, "forwarded_signals": []}

    def save() -> None:
        temporary = directory / "process.json.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, indent=2)
        temporary.replace(directory / "process.json")

    save()
    try:
        # Inherit the original stdin/stdout descriptors without parsing MCP.
        process = subprocess.Popen(command, stderr=subprocess.PIPE)
    except OSError as error:
        state.update(returncode=127, launch_error=str(error), finished_at=datetime.now(timezone.utc).isoformat())
        save()
        return 127
    state["child_pid"] = process.pid
    save()

    def forward(signum: int, _frame: object) -> None:
        state["forwarded_signals"].append(signum)
        try:
            process.send_signal(signum)
        except ProcessLookupError:
            pass

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, forward)

    def stderr() -> None:
        fd = os.open(directory / "stderr.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as log:
            saved = 0
            while chunk := process.stderr.read1(8192):
                state["stderr_bytes"] += len(chunk)
                if saved < STDERR_LIMIT:
                    kept = chunk[:STDERR_LIMIT - saved]
                    log.write(kept)
                    log.flush()
                    saved += len(kept)
                try:
                    sys.stderr.buffer.write(chunk)
                    sys.stderr.buffer.flush()
                except OSError:
                    pass  # Still drain/log the child after the provider closes.
            state["stderr_truncated"] = state["stderr_bytes"] > STDERR_LIMIT

    reader = threading.Thread(target=stderr, daemon=True)
    reader.start()
    returncode = process.wait()
    reader.join(timeout=5)
    state.update(returncode=returncode, stderr_complete=not reader.is_alive(),
                 finished_at=datetime.now(timezone.utc).isoformat())
    save()
    return returncode if returncode >= 0 else 128 - returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a child command is required")
    return run(command, args.directory)


if __name__ == "__main__":
    raise SystemExit(main())
