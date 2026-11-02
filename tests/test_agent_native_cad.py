"""Native editor reachability in isolated agent sessions, no browser calls."""
import json
from pathlib import Path
import tomllib

import pytest

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
    assert {'api_catalog', 'api_request', 'api_write', 'api_read', 'api_upload'} <= set(tools)
    assert not {'evaluate', 'feature_studio'} & set(tools)


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


def test_bridge_document_management_has_no_export_or_featurescript_fallback(monkeypatch):
    import pytest
    from r3_onshape.native import NativeCadTransport
    from r3_model.pulled import PullError
    from r3_onshape.policy import FeatureScriptForbidden
    monkeypatch.setattr(NativeCadTransport, '__init__', lambda self: None)
    calls = []
    def request(self, method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        return b'{"id":"synthetic-document"}'
    monkeypatch.setattr(NativeCadTransport, '_request', request)
    native = NativeCadTransport()
    for method, endpoint, body in [
        ('POST', '/api/v9/documents', {'name':'Synthetic coupon', 'parentId':'folder'}),
        ('POST', '/api/v9/documents/d/doc/versions', {'name':'Pinned', 'documentId':'doc', 'workspaceId':'workspace', 'microversionId':'microversion'}),
        ('DELETE', '/api/v9/documents/doc', None),
    ]:
        assert native.api_request(method, endpoint, body)['id'] == 'synthetic-document'
    assert len(calls) == 3
    for endpoint in ('/api/v17/parts/d/doc/v/version/e/element/partid/part/stl',
                     '/api/v17/partstudios/d/doc/v/version/e/element/translations',
                     '/api/v9/documents/d/doc/v/version/translate'):
        with pytest.raises(PullError, match='Geometry exports'):
            native.api_request('POST', endpoint)
    with pytest.raises(FeatureScriptForbidden):
        native.api_request('POST', '/api/v17/partstudios/d/doc/w/workspace/e/element/featurescript')
    assert len(calls) == 3
    result = mcp_server.r3_onshape_native('api_request', {'method':'POST', 'endpoint':'/api/v9/documents',
                                                       'body':{'name':'Synthetic coupon','parentId':'folder'}})
    assert result['ok'] and len(calls) == 4


def test_explicit_scoped_visibility_consent_reaches_both_providers(tmp_path,monkeypatch):
    monkeypatch.setenv('R3_ONSHAPE_DOCUMENT_VISIBILITY','public')
    monkeypatch.setenv('R3_ONSHAPE_DOCUMENT_FOLDER_ID','1234567890abcdef12345678')
    conf=config(tmp_path,monkeypatch)
    codex=agents.codex_command('codex',tmp_path,conf)
    instruction=next(x for x in codex if x.startswith('developer_instructions='))
    assert 'isPublic=true' in instruction and '1234567890abcdef12345678' in instruction
    assert 'NEW public Onshape documents only' in instruction
    claude=agents.claude_command('claude',tmp_path/'config.json')
    assert 'GitHub repository privacy is separate' in claude[claude.index('--append-system-prompt')+1]
    assert 'isPublic=true' not in agents.SYSTEM_PROMPT


def test_visibility_consent_is_never_inferred_or_unscoped(monkeypatch):
    import pytest
    monkeypatch.delenv('R3_ONSHAPE_DOCUMENT_VISIBILITY',raising=False)
    assert agents.session_instructions()==agents.SYSTEM_PROMPT
    monkeypatch.setenv('R3_ONSHAPE_DOCUMENT_VISIBILITY','public')
    monkeypatch.delenv('R3_ONSHAPE_DOCUMENT_FOLDER_ID',raising=False)
    with pytest.raises(ValueError,match='folder ID'):agents.session_instructions()


@pytest.mark.parametrize('method,resource', [
    ('GET', '/api/v9/partstudios/**/features'),
    ('POST', '/native/api/v17/partstudios/**/translations'),
])
def test_transport_backoff_is_scoped_and_reaches_both_providers(tmp_path, monkeypatch, method, resource):
    import json
    monkeypatch.setenv('R3_ONSHAPE_BACKOFFS', json.dumps([
        {'method': method, 'resource': resource, 'until': '2099-01-01T00:00:00+00:00'},
        {'method': 'GET', 'resource': '/api/v9/expired', 'until': '2000-01-01T00:00:00+00:00'}]))
    conf = config(tmp_path, monkeypatch)
    codex = agents.codex_command('codex', tmp_path, conf)
    instruction = next(x for x in codex if x.startswith('developer_instructions='))
    assert f'Known transport backoff: {method} {resource}' in instruction
    assert '/api/v9/expired' not in instruction
    assert 'preserve fresh revision guards' in instruction
    assert 'outside their scopes' in instruction
    assert 'other working REST operations and native exports' not in instruction
    claude = agents.claude_command('claude', tmp_path/'config.json')
    assert 'Known transport backoff' in claude[claude.index('--append-system-prompt')+1]
    assert '2099' not in agents.SYSTEM_PROMPT


def test_transport_backoff_requires_timezone(monkeypatch):
    import json, pytest
    monkeypatch.setenv('R3_ONSHAPE_BACKOFFS', json.dumps([
        {'method': 'GET', 'resource': '/api/v9/synthetic', 'until': '2099-01-01'}]))
    with pytest.raises(ValueError, match='timezone-aware'):
        agents.session_instructions()


def test_unknown_reset_is_scoped_without_inventing_a_deadline(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('R3_ONSHAPE_BACKOFFS', json.dumps([
        {'method': 'GET', 'resource': '/api/v*/partstudios/**/features', 'until': None}]))
    conf = config(tmp_path, monkeypatch)
    codex = agents.codex_command('codex', tmp_path, conf)
    instruction = next(x for x in codex if x.startswith('developer_instructions='))
    assert 'no reported reset deadline' in instruction
    assert 'Do not retry matching resources' in instruction
    assert 'outside their scopes' in instruction
    assert 'preserve fresh revision guards' in instruction
    claude = agents.claude_command('claude', tmp_path/'config.json')
    assert 'no reported reset deadline' in claude[claude.index('--append-system-prompt')+1]


def test_claude_session_shares_the_codex_read_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    project = tmp_path/'projects'/'robot'
    project.mkdir(parents=True)
    photo = tmp_path/'attachments'/'reference.jpg'
    photo.parent.mkdir()
    photo.write_bytes(b'image')
    settings = agents.claude_settings(project, {}, granted=[photo])
    platform = Path(agents.__file__).resolve().parents[4]
    allow, deny = settings['permissions']['allow'], settings['permissions']['deny']
    assert f'Read(/{project.resolve()}/**)' in allow and f'Read(/{platform}/**)' in allow
    assert f'Read(/{photo.resolve()})' in allow
    assert [rule for rule in allow if rule.startswith('Edit')] == [f'Edit(/{project.resolve()}/**)']
    assert f'Read(/{platform}/.git/**)' in deny
    filesystem = settings['sandbox']['filesystem']
    assert filesystem['allowWrite'] == [str(project.resolve())]
    assert str(tmp_path.resolve()) in filesystem['denyRead'] and str(photo.resolve()) in filesystem['allowRead']
    assert settings['sandbox']['allowUnsandboxedCommands'] is False
    # Oversized tool results are saved under the CLI's directory for this project only.
    results = tmp_path.resolve()/'.claude'/'projects'/str(project.resolve()).replace('/', '-').replace('.', '-').replace('_', '-')
    assert f'Read(/{results}/**)' in allow and str(results) in filesystem['allowRead']
    assert str(tmp_path.resolve()/'.claude') not in filesystem['allowRead']


def test_claude_settings_hide_sibling_projects_but_not_the_session_project():
    platform = Path(agents.__file__).resolve().parents[4]
    examples = platform/'examples'/'robots'
    sibling = examples/'zz-synthetic-sibling'
    project = examples/'zz-synthetic-project'
    sibling.mkdir(parents=True, exist_ok=True)
    project.mkdir(exist_ok=True)
    try:
        deny = agents.claude_settings(project, {})['permissions']['deny']
        assert f'Read(/{sibling}/**)' in deny and f'Read(/{project}/**)' not in deny
    finally:
        sibling.rmdir()
        project.rmdir()


def test_prompt_named_files_grant_exactly_those_existing_files(tmp_path):
    photo = tmp_path/'reference image.jpg'
    photo.write_bytes(b'image')
    plain = tmp_path/'reference.jpg'
    plain.write_bytes(b'image')
    prompt = f'See {plain}. Then `{plain}` again, not {tmp_path} and not {tmp_path}/missing.png.'
    assert agents.prompt_files(prompt) == [plain.resolve()]
    assert agents.prompt_files('use /etc/not-a-real-file and foo/bar.jpg') == []


@pytest.mark.parametrize('opening,closing', [('`', '`'), ('"', '"'), ("'", "'"), ('<', '>')])
def test_prompt_named_quoted_files_support_spaces(tmp_path, opening, closing):
    photo = tmp_path/'reference image.jpg'
    photo.write_bytes(b'image')
    prompt = f'See {opening}{photo}{closing}. Then {opening}{photo}{closing} again.'
    assert agents.prompt_files(prompt) == [photo.resolve()]


def test_prompt_named_files_reach_the_codex_profile_and_tool_scope(tmp_path, monkeypatch):
    from r3_model.read_scope import LocalReadScope
    conf = config(tmp_path, monkeypatch)
    photo = tmp_path/'outside'/'reference image.jpg'
    photo.parent.mkdir()
    photo.write_bytes(b'image')
    granted = agents.prompt_files(f'See "{photo}".')
    profile = agents.codex_permission_profile(tmp_path/'project', conf, granted=granted)
    assert profile['filesystem'][str(photo.resolve())] == 'read'
    assert str(photo.parent.resolve()) not in profile['filesystem']
    (tmp_path/'project').mkdir()
    scoped = agents.mcp_config(tmp_path/'project', 'python', granted)
    scope = LocalReadScope.from_json(scoped['mcpServers']['r3']['env']['R3_LOCAL_READ_SCOPE'])
    assert scope.check(photo) == photo.resolve()
