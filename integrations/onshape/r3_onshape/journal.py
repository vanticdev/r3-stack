"""Replay acknowledged construction requests locally when resuming a CAD attempt.

Reads are recorded too: a builder must see the part list/body faces as they were at
that point in construction. After the recorded prefix, all requests use the real
transport. A 429 is never recorded as a success, retried or followed by cleanup.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from .client import OnshapeError, Transport


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1))
    tmp.replace(path)


class RequestJournal(Transport):
    def __init__(self, api: Transport, path: Path, digest: str) -> None:
        self.api, self.path, self.digest = api, path, digest
        self.name = api.name
        self.state = json.loads(path.read_text()) if path.exists() else {"digest": digest, "calls": []}
        if self.state["digest"] != digest:
            raise ValueError(f"{path}: construction inputs changed; preserve this attempt and start a new one")
        self.index = 0

    def _call(self, signature: Any, call: Any) -> bytes:
        key = hashlib.sha256(json.dumps(signature, sort_keys=True, default=str).encode()).hexdigest()
        calls = self.state["calls"]
        if self.index < len(calls):
            saved = calls[self.index]
            if saved["key"] != key:
                raise ValueError(f"{self.path}: construction diverged at request {self.index}; refusing duplicate writes")
            raw = base64.b64decode(saved["response"])
        else:
            try:
                raw = call()
            except OnshapeError as error:
                if error.status == 429:
                    self.state["quota_blocked"] = {"retry_after": error.retry_after, "reset_at": error.reset_at,
                                                   "request": signature}
                    save_json(self.path, self.state)
                raise
            calls.append({"key": key, "request": signature, "response": base64.b64encode(raw).decode()})
            self.state.pop("quota_blocked", None)
            save_json(self.path, self.state)
        self.index += 1
        return raw

    def request(self, method: str, path: str, query: dict[str, Any] | None = None, body: Any = None,
                accept: str = "application/json", timeout: float = 120.0) -> bytes:
        return self._call([method, path, query, body, accept],
                          lambda: self.api.request(method, path, query, body, accept, timeout))

    def upload(self, path: str, file_path: Path, fields: dict[str, Any], timeout: float = 600.0) -> Any:
        checksum = hashlib.sha256(file_path.read_bytes()).hexdigest()
        raw = self._call(["UPLOAD", path, checksum, fields],
                         lambda: json.dumps(self.api.upload(path, file_path, fields, timeout)).encode())
        return json.loads(raw)
