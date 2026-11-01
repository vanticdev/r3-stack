"""Pinned browser REST reads work without native editor bootstrap or API keys."""
import base64
import copy
import io

import numpy as np
import pytest
from PIL import Image

from r3_model.pulled import PullError
from r3_onshape.rest_snapshot import assembly, capture
from r3_onshape.pull import describe, target

URL = 'https://cad.onshape.com/documents/doc/v/pin/e/assembly'


class Rest:
    def __init__(self):
        self.calls = []
        self.definition = {'rootAssembly': {
            'documentId':'doc', 'elementId':'assembly', 'documentVersion':'pin',
            'instances':[{'id':'object', 'type':'Part', 'name':'coupon', 'documentId':'doc',
                          'elementId':'studio', 'documentMicroversion':'micro', 'partId':'solid'}],
            'occurrences':[{'path':['object'], 'transform':np.eye(4).ravel().tolist(), 'fixed':True}], 'features':[]},
            'subAssemblies':[], 'parts':[{'documentId':'doc', 'elementId':'studio', 'partId':'solid',
                'mateConnectors':[{'featureId':'connector', 'mateConnectorCS':{
                    'origin':[0,0,0], 'xAxis':[1,0,0], 'zAxis':[0,0,1]}}]}]}

    def api_request(self, method, path, query=None):
        self.calls.append((method, path, query))
        assert method == 'GET' and not path.endswith('/features')
        if '/versions/' in path: return {'microversion':'micro'}
        if path.endswith('/massproperties'):
            from native_mass_fixture import uniform_box
            return {'bodies': {'solid': uniform_box(1200)}}
        if '/parts/' in path: return [{'partId':'solid','name':'coupon','material':{'displayName':'Synthetic','density':1200}}]
        if path.endswith('/shadedviews'):
            out=io.BytesIO(); Image.new('RGB',(2,2)).save(out,format='PNG')
            return {'images':[[base64.b64encode(out.getvalue()).decode()]]}
        assert '/assemblies/' in path
        assert query['includeMateFeatures'] and query['includeMateConnectors']
        return copy.deepcopy(self.definition)


def test_rest_complete_pinned_snapshot_and_connectors():
    rest=Rest(); snapshot=assembly(rest,URL)
    manifest=describe(snapshot,target(URL),{})
    assert manifest['microversion']=='micro'
    assert manifest['parts']['coupon']['density']==1200
    assert manifest['parts']['coupon']['onshape']['version_id']=='pin'
    assert np.array_equal(manifest['parts']['coupon']['mate_connectors']['connector'],np.eye(4))
    assert len(rest.calls)==4
    assert all('/v/pin/' in path or '/versions/pin' in path for _,path,_ in rest.calls)


def test_nested_occurrences_retain_absolute_transforms():
    rest=Rest(); child=rest.definition['rootAssembly'];part=child['instances'][0]
    rest.definition={'rootAssembly':{'documentId':'doc','elementId':'assembly','documentVersion':'pin',
        'instances':[{'id':'nested','type':'Assembly','documentId':'doc','elementId':'sub','documentVersion':'pin'}],
        'occurrences':[{'path':['nested'],'transform':np.eye(4).ravel().tolist()},
                       {'path':['nested','object'],'transform':np.eye(4).ravel().tolist()}], 'features':[]},
        'subAssemblies':[{'documentId':'doc','elementId':'sub','documentVersion':'pin','instances':[part],'features':[]}],
        'parts':rest.definition['parts']}
    manifest=describe(assembly(rest,URL),target(URL),{})
    assert manifest['parts']['coupon']['onshape']['occurrence_path']==['nested','object']


@pytest.mark.parametrize('bad',['transform','part','external_version','mesh'])
def test_incomplete_rest_data_is_blocking(bad):
    rest=Rest()
    if bad=='transform': rest.definition['rootAssembly']['occurrences']=[]
    elif bad=='part': rest.definition['parts']=[]
    elif bad=='external_version':
        rest.definition['rootAssembly']['instances'][0]['documentId']='external'
        rest.definition['parts'][0]['documentId']='external'
    elif bad=='mesh':
        original=rest.api_request
        def request(method,path,query=None):
            if '/parts/' in path:return [{'partId':'solid','isMesh':True}]
            return original(method,path,query)
        rest.api_request=request
    with pytest.raises(PullError): assembly(rest,URL)


def test_pinned_rest_image_requires_no_active_editor():
    rest=Rest(); data,evidence=capture(rest,URL)
    assert data.startswith(b'\x89PNG') and evidence['microversion']=='micro'
    assert evidence['version_id']=='pin' and len(rest.calls)==2


def test_step_translation_uses_string_part_selection_and_result_document(monkeypatch):
    import json
    from r3_onshape.native import NativeCadTransport
    monkeypatch.setattr(NativeCadTransport,'__init__',lambda self:None)
    calls=[]
    def request(self,method,path,body=None,**kwargs):
        calls.append((method,path,body))
        if method=='POST':
            assert body['partIds']=='solid'
            return json.dumps({'requestState':'DONE','id':'job','resultDocumentId':'result-doc',
                               'resultExternalDataIds':['file']}).encode()
        assert path=='/native/api/v17/documents/d/result-doc/externaldata/file'
        return b'ISO-10303-21; synthetic transport bytes'
    monkeypatch.setattr(NativeCadTransport,'_request',request)
    assert NativeCadTransport().step('doc','pin','studio','solid').startswith(b'ISO-10303')
    assert len(calls)==2 and not any('/features' in path for _,path,_ in calls)


@pytest.mark.parametrize('status',['OK','ERROR',None])
def test_rest_mate_regeneration_must_be_verified(status):
    rest=Rest()
    rest.definition['rootAssembly']['features']=[{'id':'mate','featureData':{'mateType':'FASTENED'}}]
    original=rest.api_request
    def request(method,path,query=None):
        if path.endswith('/features'):
            return {'featureStates':{'mate':{'featureStatus':status}}}
        return original(method,path,query)
    rest.api_request=request
    if status=='OK': assert assembly(rest,URL)['definition']['rootAssembly']['features']
    else:
        with pytest.raises(PullError,match='not verified OK'): assembly(rest,URL)
