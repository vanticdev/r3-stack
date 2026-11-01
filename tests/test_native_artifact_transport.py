"""Read exact authenticated cached responses without companion filesystem access."""
import hashlib
import io
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest

from r3_model.pulled import PullError
from r3_onshape.native import NativeCadTransport


def client(tmp_path, raw):
    (tmp_path / 'bridge.json').write_text(json.dumps({'host': '127.0.0.1', 'port': 8766, 'token': 'synthetic-token'}))
    value = NativeCadTransport(tmp_path)
    value._opener = Mock()
    value._opener.open.return_value = io.BytesIO(raw)
    return value


def test_complete_cached_response_is_one_authenticated_local_read(tmp_path):
    raw = json.dumps({'fixture': [{'index': i, 'text': 'synthetic' * 900} for i in range(30)]}).encode()
    handle = hashlib.sha256(raw).hexdigest() + '.json'
    value = client(tmp_path, raw)
    assert not (tmp_path / 'artifacts').exists()
    assert value.artifact(handle) == json.loads(raw)
    request = value._opener.open.call_args.args[0]
    assert request.full_url == 'http://127.0.0.1:8766/artifacts/' + handle
    assert request.get_method() == 'GET'
    assert request.get_header('Authorization') == 'Bearer synthetic-token'
    assert value._opener.open.call_count == 1


def test_corrupt_cached_response_fails_without_fallback(tmp_path):
    value = client(tmp_path, b'{"corrupt":true}')
    with pytest.raises(PullError) as error:
        value.artifact('0' * 64 + '.json')
    assert error.value.code == 'cad_native_invalid'
    assert 'checksum' in str(error.value).lower()
    assert value._opener.open.call_count == 1


@pytest.mark.parametrize('handle', ['bridge.json', '../bridge.json', 'A' * 64 + '.json', 'a' * 64 + '.bin'])
def test_invalid_handles_never_make_a_request(tmp_path, handle):
    value = client(tmp_path, b'{}')
    with pytest.raises(PullError):
        value.artifact(handle)
    value._opener.open.assert_not_called()


@pytest.mark.parametrize('method,path', [
    ('GET', '/artifacts'), ('GET', '/artifacts/bridge.json'),
    ('POST', '/artifacts/' + 'a' * 64 + '.json'),
    ('GET', '/artifacts/' + 'a' * 64 + '.json/../bridge.json'),
])
def test_cache_routes_cannot_enumerate_write_or_traverse(tmp_path, method, path):
    value = client(tmp_path, b'{}')
    with pytest.raises(PullError) as error:
        value._request(method, path)
    assert error.value.code == 'cad_native_route_forbidden'
    value._opener.open.assert_not_called()


@pytest.mark.skipif(sys.platform != 'darwin' or shutil.which('codex') is None,
                   reason='native session filesystem probe requires macOS and Codex')
def test_isolated_session_reads_exact_cached_response_without_cache_filesystem_access(tmp_path):
    from r3_orchestrator.api.agents import codex_permission_profile, session_environment, _toml

    root = tmp_path / 'synthetic-project'
    root.mkdir()
    runtime = tmp_path / 'private-companion'
    cache = runtime / 'artifacts'
    cache.mkdir(parents=True)
    raw = json.dumps({'fixture': 'unrelated cached response'}).encode()
    handle = hashlib.sha256(raw).hexdigest() + '.json'
    cached_file = cache / handle
    cached_file.write_bytes(raw)
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            assert self.headers.get('Authorization') == 'Bearer synthetic-token'
            assert self.path == '/artifacts/' + handle
            self.send_response(200)
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (runtime / 'bridge.json').write_text(json.dumps({
        'host': '127.0.0.1', 'port': server.server_port, 'token': 'synthetic-token'}))
    config = {'mcpServers': {'onshape_native': {'env': {'ONSHAPE_NATIVE_RUNTIME': str(runtime)}}}}
    env = session_environment(root, root / 'build/agent/synthetic', config)
    sandbox = ['codex', 'sandbox', '--permission-profile', 'r3_session', '-C', str(root), '-c',
               'permissions.r3_session=' + _toml(codex_permission_profile(root, config)), '--']
    try:
        denied = subprocess.run([*sandbox, '/bin/cat', str(cached_file)], cwd=root, env=env,
                                capture_output=True, text=True, timeout=30)
        assert denied.returncode != 0 and not denied.stdout
        script = 'from r3_onshape.native import NativeCadTransport; print(NativeCadTransport().artifact(' + repr(handle) + ')["fixture"])'
        result = subprocess.run([*sandbox, sys.executable, '-c', script], cwd=root, env=env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'unrelated cached response'
        assert calls == ['/artifacts/' + handle]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
