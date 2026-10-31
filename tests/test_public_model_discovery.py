"""Public discovery is metadata-only, provenance-preserving and project-independent."""
from r3_catalog.modelbank.ingest import ingest_onshape
from r3_orchestrator import mcp_server


class Browser:
    def __init__(self):self.calls=[]
    def json(self,method,path,query=None):
        self.calls.append((method,path,query))
        assert method=='GET' and not any(x in path for x in ('/features','/stl','translations'))
        if path=='/documents':
            assert query['filter']==4  # OpenAPI: 2=Shared, 4=Public.
            assert 1 <= query['limit'] <= 20
            return {'items':[{'id':'public','name':'Synthetic coupon','public':True},
                             {'id':'private','name':'Unrelated private object','public':False},
                             {'id':'unversioned','name':'Unversioned','isPublic':True}]}
        if path.endswith('/versions'):
            if 'unversioned' in path:return []
            return [{'id':'old','createdAt':'2025-01-01'},{'id':'new','createdAt':'2026-01-01'}]
        return [{'id':'studio','name':'Synthetic solid','elementType':'PARTSTUDIO'},
                {'id':'assembly','name':'Other tab','elementType':'ASSEMBLY'}]


def test_public_alias_field_and_latest_version_without_geometry_or_private_reads():
    browser=Browser();models=ingest_onshape('Synthetic coupon',browser)
    assert len(models)==1 and models[0].sources[0].vid=='new'
    assert models[0].validation.status=='unverified'
    assert not any('/private/' in path for _,path,_ in browser.calls)
    assert models[0].searched_sources[0]['query']=='Synthetic coupon'


def test_mcp_exposes_readonly_candidates_without_bank_writes(monkeypatch,tmp_path):
    from r3_onshape.native import NativeCadTransport
    monkeypatch.setattr(NativeCadTransport,'__init__',lambda self:None)
    browser=Browser()
    monkeypatch.setattr(NativeCadTransport,'api_request',lambda self,method,path,query=None:browser.json(method,path.removeprefix('/api/v9'),query))
    monkeypatch.setenv('R3_MODEL_BANK',str(tmp_path/'bank'))
    monkeypatch.setenv('R3_MODEL_CACHE',str(tmp_path/'cache'))
    monkeypatch.setenv('R3_PROJECT',str(tmp_path/'project'))
    result=mcp_server.r3_models_search_public('Synthetic coupon')
    assert result['ok'] and len(result['models'])==1
    assert not (tmp_path/'bank').exists() and not (tmp_path/'project/catalog/modelbank').exists()
    assert 'r3_models_search_public' in mcp_server.ungated_tools()


def test_invalid_public_search_limit_makes_no_request():
    import pytest
    browser = Browser()
    for limit in (0, 21):
        with pytest.raises(ValueError, match="between 1 and 20"):
            ingest_onshape("Synthetic coupon", browser, limit)
    assert not browser.calls
