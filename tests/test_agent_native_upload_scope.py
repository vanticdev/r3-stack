"""Project-scoped MCP file uploads do not inherit a broader account policy."""
import copy
import json

from r3_orchestrator.api import agents


def test_native_upload_scope_matches_project_and_platform_read_boundaries(tmp_path, monkeypatch):
    platform = tmp_path / 'platform'
    project = platform / 'examples/robots/selected'
    monkeypatch.setattr(agents, '__file__', str(platform / 'services/orchestrator/r3_orchestrator/api/agents.py'))
    account = {'command': 'synthetic-native', 'env': {'PRESERVED': 'yes',
        'ONSHAPE_NATIVE_UPLOAD_SCOPE': 'unrelated account scope'}}
    original = copy.deepcopy(account)
    monkeypatch.setattr(agents, 'native_mcp_config', lambda: account)
    config = agents.mcp_config(project)
    env = config['mcpServers']['onshape_native']['env']
    rules = json.loads(env['ONSHAPE_NATIVE_UPLOAD_SCOPE'])['rules']
    assert rules == [
        {'path': str(platform.resolve()), 'access': 'read'},
        {'path': str((platform / 'examples/robots').resolve()), 'access': 'deny'},
        {'path': str((platform / '.git').resolve()), 'access': 'deny'},
        {'path': str(project.resolve()), 'access': 'read'},
    ]
    assert env['PRESERVED'] == 'yes' and account == original
    assert config['mcpServers']['r3']['env']['R3_LOCAL_READ_SCOPE'] == env['ONSHAPE_NATIVE_UPLOAD_SCOPE']


def test_native_upload_scope_uses_resolved_selected_project(tmp_path, monkeypatch):
    selected = tmp_path / 'selected'
    selected.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(selected, target_is_directory=True)
    monkeypatch.setattr(agents, 'native_mcp_config', lambda: {'command': 'synthetic', 'env': {}})
    config = agents.mcp_config(alias)
    rules = json.loads(config['mcpServers']['onshape_native']['env']['ONSHAPE_NATIVE_UPLOAD_SCOPE'])['rules']
    assert rules[-1] == {'path': str(selected.resolve()), 'access': 'read'}


def test_r3_local_file_tools_remain_scoped_without_native_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, 'native_mcp_config', lambda: None)
    config = agents.mcp_config(tmp_path / 'selected')
    assert 'onshape_native' not in config['mcpServers']
    rules = json.loads(config['mcpServers']['r3']['env']['R3_LOCAL_READ_SCOPE'])['rules']
    assert rules[-1] == {'path': str((tmp_path / 'selected').resolve()), 'access': 'read'}
