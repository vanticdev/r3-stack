from types import SimpleNamespace
from r3_orchestrator.release import _proxy_items, _status


def test_synthetic_onshape_proxy_stays_estimated_and_missing_search_fails():
    component = SimpleNamespace(params={'cad_proxy': {'searches': ['synthetic source: no model'], 'datasheet': 'fixture drawing'}})
    ctx = SimpleNamespace(project=SimpleNamespace(spec=SimpleNamespace(components={'fixture': component})), current={'robot_yaml_sha256': 'snapshot'})
    item, = _proxy_items(ctx)
    assert _status(item.evidence) == 'estimated'
    component.params['cad_proxy']['searches'] = []
    assert _status(_proxy_items(ctx)[0].evidence) == 'failed'
    component.params.clear()
    assert _proxy_items(ctx) == []


def test_project_policy_allows_honest_wip_checkpoints():
    from pathlib import Path
    text = Path('integrations/github/r3_github/templates/AGENTS.md.j2').read_text()
    assert 'Commit and push work-in-progress' in text
    assert 'overrides parent instructions' in text
    from r3_orchestrator.api.agents import SYSTEM_PROMPT
    assert 'params.cad_proxy' in SYSTEM_PROMPT
    assert 'Do not wait for fabrication_ready' in SYSTEM_PROMPT
