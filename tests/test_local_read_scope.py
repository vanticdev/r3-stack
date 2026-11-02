"""Local tool source inputs honor the project boundary before opening files."""
import json

import pytest

from r3_catalog.modelbank import fetch
from r3_model.read_scope import LocalReadScope, ReadRule, local_source


@pytest.fixture
def files(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    project = platform / "examples/robots/selected"
    other = platform / "examples/robots/other"
    outside = tmp_path / "unrelated"
    history = platform / ".git"
    for path in (project, other, outside, history):
        path.mkdir(parents=True, exist_ok=True)
    sources = {}
    for name, parent in (("project", project), ("shared", platform), ("other", other),
                         ("outside", outside), ("history", history)):
        sources[name] = parent / "unrelated fixture.step"
        sources[name].write_bytes(f"synthetic canary {name}".encode())
    sources["escape"] = project / "escape.step"
    sources["escape"].symlink_to(sources["outside"])
    scope = LocalReadScope((ReadRule(platform, "read"), ReadRule(platform / "examples/robots", "deny"),
                            ReadRule(history, "deny"), ReadRule(project, "read")))
    monkeypatch.setenv("R3_LOCAL_READ_SCOPE", scope.to_json())
    return sources, scope, tmp_path / "download.step"


@pytest.mark.parametrize("name", ["other", "outside", "history", "escape"])
def test_local_model_url_is_rejected_before_urlopen_or_target_write(files, monkeypatch, name):
    sources, scope, target = files
    calls = []
    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda *a, **kw: calls.append(a))
    with pytest.raises(ValueError, match="local read scope"):
        fetch.download(sources[name].as_uri(), target)
    assert calls == [] and not target.exists()


@pytest.mark.parametrize("name", ["project", "shared"])
def test_scoped_local_download_preserves_bytes_and_handles_encoded_paths(files, name):
    sources, scope, target = files
    assert fetch.download(sources[name].as_uri(), target) == target
    assert target.read_bytes() == sources[name].read_bytes()


def test_standalone_local_download_remains_explicitly_available(files, monkeypatch):
    sources, scope, target = files
    monkeypatch.delenv("R3_LOCAL_READ_SCOPE")
    fetch.download(sources["outside"].as_uri(), target)
    assert target.read_bytes() == sources["outside"].read_bytes()


def test_remote_download_is_not_restricted_as_a_local_source(files, monkeypatch):
    sources, scope, target = files
    requests = []
    class Response:
        def __enter__(self):
            self.data = iter((b"synthetic remote payload", b""))
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            return next(self.data)
    def open_url(request, **kwargs):
        requests.append(request.full_url)
        return Response()
    monkeypatch.setattr(fetch.urllib.request, "urlopen", open_url)
    fetch.download("https://example.invalid/fixture.step", target)
    assert requests == ["https://example.invalid/fixture.step"]
    assert target.read_bytes() == b"synthetic remote payload"


@pytest.mark.parametrize("raw", ["", "{}", "[]", '{"rules":[]}',
    '{"rules":[{"path":"relative","access":"read"}]}',
    '{"rules":[{"path":"/synthetic","access":"write"}]}'])
def test_malformed_configured_read_scope_fails_closed(files, monkeypatch, raw):
    sources, scope, target = files
    monkeypatch.setenv("R3_LOCAL_READ_SCOPE", raw)
    with pytest.raises(ValueError, match="local read scope"):
        local_source(sources["project"])
    assert not target.exists()


def test_scope_roundtrip_and_deny_ties_are_order_independent(files):
    sources, scope, target = files
    assert LocalReadScope.from_json(scope.to_json()) == scope
    assert scope.check(sources["project"]) == sources["project"]
    rules = (*scope.rules, ReadRule(sources["project"].parent, "deny"))
    for ordered in (rules, tuple(reversed(rules))):
        with pytest.raises(ValueError, match="local read scope"):
            LocalReadScope(ordered).check(sources["project"])


def test_empty_scope_and_unknown_rule_fields_are_rejected():
    with pytest.raises(ValueError, match="scope"):
        LocalReadScope(())
    with pytest.raises(ValueError, match="scope"):
        LocalReadScope.from_json(json.dumps({"rules": [{"path": "/synthetic", "access": "read", "extra": True}]}))
