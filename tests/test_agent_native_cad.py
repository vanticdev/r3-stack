"""Native editor reachability in isolated agent sessions, no browser calls."""
import json
from pathlib import Path
import tomllib

from r3_orchestrator.api import agents
from r3_orchestrator import mcp_server


def config(tmp_path, monkeypatch):
    path = tmp_path / '.mcp.json'
    path.write_text(json.dumps({'mcpServers': {'onshape_native': {'command': 'synthetic-python', 'args': ['synthetic-native-server'],
        'env': {'ONSHAPE_NATIVE_RUNTIME': str(tmp_path / 'native-runtime')}}}}))
    monkeypatch.setenv('R3_ONSHAPE_NATIVE_MCP_CONFIG', str(path))
    return agents.mcp_config(tmp_path)


def test_session_has_direct_native_editor_server(tmp_path, monkeypatch):
    value = config(tmp_path, monkeypatch)
    assert value['mcpServers']['onshape_native']['command'] == 'synthetic-python'
    assert value['mcpServers']['r3']['env']['ONSHAPE_NATIVE_RUNTIME'].endswith('native-runtime')
    args = agents.codex_command('codex', tmp_path, value)
    overrides = [args[i+1] for i, arg in enumerate(args[:-1]) if arg == '-c']
    assert 'mcp_servers.onshape_native.enabled=true' in overrides
    encoded = next(x.split('=',1)[1] for x in overrides if x.startswith('mcp_servers.onshape_native.enabled_tools='))
    tools = tomllib.loads('value=' + encoded)['value']
    assert {'native_write', 'native_schema', 'assembly_feature', 'document_edit', 'capture_viewport'} <= set(tools)
    assert not {'evaluate', 'api_request', 'api_write', 'api_upload', 'feature_studio'} & set(tools)


def test_pull_replaces_generation_tools():
    tools = mcp_server.ungated_tools()
    assert 'r3_cad_pull' in tools and 'r3_onshape_native' in tools
    assert 'r3_cad_plan' not in tools and 'r3_cad_recover' not in tools
    assert 'model parts' in agents.SYSTEM_PROMPT or 'model parts' in agents.SYSTEM_PROMPT.lower()
    assert 'r3_cad_pull' in agents.SYSTEM_PROMPT


def test_proxy_preserves_native_errors(monkeypatch):
    from r3_model.pulled import PullError
    from r3_onshape.native import NativeCadTransport

    def reject(self, job):
        raise PullError('cad_native_route_forbidden', 'Public REST disabled')
    monkeypatch.setattr(NativeCadTransport, '__init__', lambda self: None)
    monkeypatch.setattr(NativeCadTransport, 'command', reject)
    result = mcp_server.r3_onshape_native('command', {'kind': 'rest'})
    assert result['ok'] is False and result['code'] == 'cad_native_route_forbidden'
