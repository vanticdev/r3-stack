"""Browser-native Onshape operations through the local onshape-native bridge.

A few organizational operations exist only in Onshape's editor protocol, not in
the public REST API (see ``docs/architecture/headless.md``):

* Part Studio feature folders (``partstudiofolders.GBTUiCreateFolder``) and
  reading their membership;
* document tab folders (``ui.document.GBTUiEditElementGroups``); REST reads them
  (``getDocumentContents``) but cannot create them;
* assembly instance and mate folders;
* feature visibility (hiding planes, sketches and mate connectors).

These go through the onshape-native companion's loopback HTTP API
(``/local/<tool>`` conveniences and ``/command`` native envelopes), which drives
the logged-in browser editor. It is a local-development path: on a server the
CAD builds headlessly and these steps are reported as skipped.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .client import OnshapeError, NativeBridgeTransport
from .quota import raise_if_quota


class BridgeUnavailable(RuntimeError):
    pass


class NativeBridge:
    def __init__(self, transport: NativeBridgeTransport | None = None) -> None:
        try:
            self.transport = transport or NativeBridgeTransport()
        except FileNotFoundError as error:
            raise BridgeUnavailable(str(error)) from None
        if not self.transport.healthy():
            raise BridgeUnavailable("onshape-native bridge is not connected to a browser")
        self.tabs: dict[str, int] = {}

    # -------------------------------------------------------------- transport
    def _post(self, path: str, body: dict[str, Any], timeout: float = 300.0) -> dict[str, Any]:
        request = urllib.request.Request(self.transport.base + path, data=json.dumps(body).encode(), method="POST",
                                         headers={**self.transport._headers(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise OnshapeError(error.code, f"bridge {path}: {error.read().decode(errors='replace')[:400]}", error.headers) from None

    READ_TOOLS = {"element_tree", "document_tree", "display_state"}

    def local(self, tool: str, args: dict[str, Any], timeout: float = 300.0) -> dict[str, Any]:
        """A /local tool call; only stale read snapshots may be retried. Quotas stop immediately."""
        for attempt in range(6):
            try:
                result = self._post(f"/local/{tool}", args, timeout)
                if result.get("error"):
                    error = RuntimeError(str(result["error"]))
                    raise_if_quota(error)
                    raise error
                return result
            except RuntimeError as error:
                raise_if_quota(error)
                transient = "changed during" in str(error)
                if not transient or tool not in self.READ_TOOLS or attempt == 5:
                    raise
                time.sleep(3.0)
        raise AssertionError("unreachable")

    @staticmethod
    def rate_limited(error: Exception) -> bool:
        return "429" in str(error)

    def command(self, job: dict[str, Any], timeout: float = 300.0) -> dict[str, Any]:
        result = self._post("/command", job, timeout)
        if isinstance(result, dict) and result.get("error"):
            error = RuntimeError(f"bridge command {job.get('kind')}: {result['error']}")
            raise_if_quota(error)
            raise error
        return result

    # -------------------------------------------------------------- editor tabs
    @staticmethod
    def url(did: str, wid: str, eid: str) -> str:
        return f"https://cad.onshape.com/documents/{did}/w/{wid}/e/{eid}"

    def open(self, did: str, wid: str, eid: str, wait: float = 90.0) -> int:
        """Open (or reuse) a background editor tab for the element and wait until it is connected."""
        url = self.url(did, wid, eid)
        if url not in self.tabs:
            opened = self.command({"kind": "open", "target": {"did": did, "wid": wid, "eid": eid}, "mode": "background", "wait_seconds": 20})
            self.tabs[url] = int(opened["tab_id"])
        deadline = time.time() + wait
        while True:
            try:
                state = self.state(did, wid, eid)
                if state.get("connected"):
                    return self.tabs[url]
            except RuntimeError as error:
                raise_if_quota(error)
                pass
            if time.time() > deadline:
                raise RuntimeError(f"Onshape editor for {url} did not connect")
            time.sleep(2.0)

    def state(self, did: str, wid: str, eid: str) -> dict[str, Any]:
        tab = self.tabs.get(self.url(did, wid, eid))
        return self.command({"kind": "state", "target": {"did": did, "wid": wid, "eid": eid}, "tab_id": tab})

    def native_write(self, did: str, wid: str, eid: str, command: str, body: dict[str, Any]) -> dict[str, Any]:
        tab = self.open(did, wid, eid)
        state = self.state(did, wid, eid)
        microversion = state.get("element_microversion") or state.get("microversion")
        key = "expected_element_microversion" if state.get("element_microversion") else "expected_microversion"
        return self.command({"kind": "native", "target": {"did": did, "wid": wid, "eid": eid}, "command": command, "body": body,
                             key: microversion, "tab_id": tab})

    def close_tabs(self) -> None:
        for url, tab in list(self.tabs.items()):
            did, wid, eid = url.split("/documents/")[1].replace("/w/", "/").replace("/e/", "/").split("/")
            try:
                self.command({"kind": "editor_tab", "target": {"did": did, "wid": wid, "eid": eid}, "tab_id": tab, "action": "close"})
            except RuntimeError as error:
                raise_if_quota(error)
                pass
        self.tabs.clear()

    # -------------------------------------------------------------- trees
    def _snapshot_rows(self, first: dict[str, Any], tool: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        """All rows of a tree snapshot: read the companion's saved snapshot artifact when it is on
        this machine (assembly rows are large), else page through it."""
        runtime = getattr(self.transport, "runtime", None)
        if runtime is not None and first.get("snapshot"):
            path = runtime / "artifacts" / first["snapshot"]
            if path.exists():
                data = json.loads(path.read_text())
                if isinstance(data, dict) and isinstance(data.get("rows"), list):
                    return data["rows"]
        rows = list(first.get("data") or [])
        offset = first.get("next_offset")
        while offset is not None:
            page = self.local(tool, {**args, "snapshot": first["snapshot"], "offset": offset, "limit": 3})
            rows.extend(page.get("data") or [])
            offset = page.get("next_offset")
        return rows

    def element_tree(self, did: str, wid: str, eid: str) -> dict[str, Any]:
        """Complete element tree: Part Studio features/folders or assembly occurrences and sidebar."""
        args = {"url": self.url(did, wid, eid)}
        first = self.local("element_tree", {**args, "limit": 1})
        return {**{k: v for k, v in first.items() if k != "data"}, "rows": self._snapshot_rows(first, "element_tree", args)}

    def document_tree(self, did: str, wid: str) -> dict[str, Any]:
        args = {"url": f"https://cad.onshape.com/documents/{did}/w/{wid}"}
        first = self.local("document_tree", {**args, "limit": 1})
        return {**{k: v for k, v in first.items() if k != "data"}, "rows": self._snapshot_rows(first, "document_tree", args)}

    # -------------------------------------------------------------- organization writes
    def feature_folder(self, did: str, wid: str, eid: str, name: str, feature_ids: list[str]) -> dict[str, Any]:
        tree = self.element_tree(did, wid, eid)
        folders = {r["id"]: r.get("name") for r in tree["rows"] if str(r.get("id", "")).startswith("feature_folder:")}
        inside = {r.get("feature_id") for r in tree["rows"] if r.get("parent_id") in folders and folders[r["parent_id"]] == name}
        if set(feature_ids) <= inside:
            return {"structure_verified": True, "already": True}
        result = self.local("sidebar_edit", {"snapshot": tree["snapshot"], "action": "create_folder", "name": name,
                                             "node_ids": [f"feature:{fid}" for fid in feature_ids]})
        if not result.get("structure_verified"):
            raise RuntimeError(f"feature folder {name!r} not verified: {json.dumps(result)[:300]}")
        return result

    def assembly_folder(self, did: str, wid: str, eid: str, name: str, node_ids: list[str]) -> dict[str, Any]:
        tree = self.element_tree(did, wid, eid)
        loose = {r["id"] for r in tree["rows"] if r.get("parent_id") in ("assembly_sidebar", 'assembly_node:["mateList"]')}
        node_ids = [n for n in node_ids if n in loose]  # already foldered by an earlier run
        if not node_ids:
            return {"structure_verified": True, "already": True}
        result = self.local("sidebar_edit", {"snapshot": tree["snapshot"], "action": "create_folder", "name": name, "node_ids": node_ids})
        if not result.get("structure_verified"):
            raise RuntimeError(f"assembly folder {name!r} not verified: {json.dumps(result)[:300]}")
        return result

    def tab_folders(self, did: str, wid: str, groups: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
        """Create (or reuse) document tab folders in order and move the listed element ids into each."""
        out = []
        for name, element_ids in groups:
            rows = self.document_tree(did, wid)["rows"]
            folder = next((r["id"] for r in rows if r.get("name") == name and str(r.get("id", "")).startswith("folder:")), None)
            if folder is None:
                created = self.local("document_edit", {"snapshot": self.document_tree(did, wid)["snapshot"], "action": "create_folder",
                                                       "name": name, "parent_id": "root"})
                if not created.get("verified"):
                    raise RuntimeError(f"tab folder {name!r} not verified: {json.dumps(created)[:300]}")
                rows = self.document_tree(did, wid)["rows"]
                folder = next(r["id"] for r in rows if r.get("name") == name and str(r.get("id", "")).startswith("folder:"))
            for eid in element_ids:
                tree = self.document_tree(did, wid)
                row = next((r for r in tree["rows"] if r.get("id") == f"element:{eid}"), None)
                if row is not None and row.get("parent_id") == folder:
                    continue
                moved = self.local("document_edit", {"snapshot": tree["snapshot"], "action": "move", "node_id": f"element:{eid}",
                                                     "parent_id": folder})
                if not moved.get("verified"):
                    raise RuntimeError(f"moving {eid} into {name!r} not verified: {json.dumps(moved)[:300]}")
            out.append({"folder": name, "elements": element_ids})
        return out

    def hide_partstudio_features(self, did: str, wid: str, eid: str, feature_ids: list[str]) -> dict[str, Any]:
        """Hide planes, sketches and mate connectors of a Part Studio (the editor's
        ``GBTSetFeatureVisibility`` inside a batch visibility change)."""
        body = {"elementId": eid, "editDescription": "Hide reference geometry",
                "featureVisibilityCalls": [{"$type": "ui.GBTSetFeatureVisibility", "elementId": eid, "featureIds": feature_ids,
                                            "visibility": 1, "editDescription": "Hide reference geometry"}]}
        return self.native_write(did, wid, eid, "ui.assembly.GBTUiBatchVisibilityChange", body)

    def display_rows(self, did: str, wid: str, eid: str) -> dict[str, Any]:
        tab = self.open(did, wid, eid)
        args = {"url": self.url(did, wid, eid), "tab_id": tab}
        first = self.local("display_state", {**args, "limit": 1})
        return {**{k: v for k, v in first.items() if k != "data"}, "rows": self._snapshot_rows(first, "display_state", args)}

    def set_visibility(self, snapshot: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
        return self.local("set_visibility", {"snapshot": snapshot, "changes": changes})


def connect() -> NativeBridge | None:
    """The bridge when it is configured, connected and allowed (``R3_CAD_NO_BRIDGE=1`` disables it)."""
    import os

    if os.environ.get("R3_CAD_NO_BRIDGE") == "1":
        return None
    try:
        return NativeBridge()
    except BridgeUnavailable:
        return None
