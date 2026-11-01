"""Synthetic exported geometry retains independently assigned native inertia."""
import copy
from pathlib import Path

import numpy as np
import pytest

from r3_model.pulled import PullError
from r3_onshape.pull import describe, download, target
from r3_onshape.rest_snapshot import assembly

URL='https://cad.onshape.com/documents/doc/v/pin/e/assembly'
INERTIA=np.array([[2e-6,1e-7,2e-7],[1e-7,3e-6,3e-7],[2e-7,3e-7,4e-6]])
CENTER=[.004,.008,.012]


def bounded(values):
    a=np.asarray(values,float).reshape(-1)
    return np.concatenate([a,a-1e-12,a+1e-12]).tolist()


def properties():
    return {'mass':bounded([.012]),'volume':bounded([6e-6]),'centroid':bounded(CENTER),
            'inertia':bounded(INERTIA),'hasMass':True,'massMissingCount':0}


def snapshot():
    ref={'documentId':'doc','documentVersion':'pin','documentMicroversion':'revision',
         'elementId':'studio','partId':'solid'}
    part={**ref,'material':{'name':'Synthetic material','density':1000},
          'mateConnectors':[{'featureId':'origin','mateConnectorCS':{
              'origin':[0,0,0],'xAxis':[1,0,0],'zAxis':[0,0,1]}}]}
    instance={**ref,'id':'instance','name':'coupon','type':'Part'}
    definition={'parts':[part],'subAssemblies':[],'rootAssembly':{
        'documentId':'doc','documentVersion':'pin','elementId':'assembly',
        'instances':[instance],'occurrences':[{'path':['instance'],'transform':np.eye(4).ravel().tolist()}], 'features':[]}}
    return {'microversion':'revision','definition':definition,'rows':[{'kind':'occurrence','instance_type':'Part',
        'occurrence_path':['instance'],'name':'coupon','reference':ref,'transform':np.eye(4).ravel().tolist()}]}


class Rest:
    def __init__(self):self.calls=[];self.definition=snapshot()['definition']
    def api_request(self,method,path,query=None):
        self.calls.append((method,path,query));assert method=='GET' and '/features' not in path
        if path.endswith('/massproperties'):
            assert '/v/pin/' in path
            assert query['useMassPropertyOverrides'] is True
            assert query['inferMetadataOwner'] is False
            return {'microversionId':'revision','bodies':{self.definition['parts'][0]['partId']:properties()}}
        if '/versions/' in path:return {'microversion':'revision'}
        if '/parts/' in path:return [{'partId':self.definition['parts'][0]['partId'],'material':{'name':'Synthetic material','density':1000}}]
        return copy.deepcopy(self.definition)


def test_pinned_rest_reads_native_overrides_once_per_unique_part():
    rest=Rest();root=rest.definition['rootAssembly'];other=copy.deepcopy(root['instances'][0]);other.update(id='other',name='other');root['instances'].append(other)
    root['occurrences'].append({'path':['other'],'transform':np.eye(4).ravel().tolist()})
    result=assembly(rest,URL)
    assert result['definition']['parts'][0]['native_mass_properties']==properties()
    assert len(rest.calls)==4
    assert sum(path.endswith('/massproperties') for _,path,_ in rest.calls)==1


@pytest.fixture
def exported(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.Interface import Interface_Static
    import trimesh
    step=tmp_path/'synthetic.step'
    Interface_Static.SetCVal_s('xstep.cascade.unit','MM')
    writer=STEPControl_Writer();Interface_Static.SetCVal_s('write.step.unit','MM')
    writer.Transfer(BRepPrimAPI_MakeBox(10,20,30).Shape(),STEPControl_AsIs);writer.Write(str(step))
    mesh=trimesh.creation.box([.01,.02,.03]);mesh.apply_translation([.005,.01,.015])
    return step.read_bytes(),mesh.export(file_type='stl')


class Files:
    def __init__(self,exported):self.exported=exported;self.calls=[]
    def step(self,*args):self.calls.append('step');return self.exported[0]
    def stl(self,*args):self.calls.append('stl');return self.exported[1]
    def capture(self,url):return b'\x89PNG\r\n\x1a\nsynthetic',{'microversion':'revision'}


def manifest():
    result=describe(snapshot(),target(URL),{})
    result['parts']['coupon']['native_mass_properties']=properties()
    return result


def test_step_verification_does_not_overwrite_native_mass_center_or_tensor(tmp_path,exported):
    result=manifest();transport=Files(exported);folder=tmp_path/'download';folder.mkdir()
    download(transport,result,folder,URL)
    part=result['parts']['coupon']
    assert part['mass']==.012 # STEP uniform-density mass is .006 kg.
    np.testing.assert_array_equal(part['com'],CENTER)
    np.testing.assert_array_equal(np.array(part['inertia']).reshape(3,3),INERTIA)
    assert part['native_mass_properties']==properties()
    assert (folder/part['step']).read_bytes()==exported[0]
    assert (folder/part['stl']).read_bytes()==exported[1]
    assert sorted(transport.calls)==['step','stl']


@pytest.mark.parametrize('bad',['missing','partial','no_mass','negative_mass','invalid_tensor'])
def test_invalid_native_mass_never_falls_back_or_spends_export_quota(tmp_path,exported,bad):
    result=manifest();part=result['parts']['coupon'];props=part['native_mass_properties']
    if bad=='missing':part.pop('native_mass_properties')
    elif bad=='partial':props['massMissingCount']=1
    elif bad=='no_mass':props['hasMass']=False
    elif bad=='negative_mass':props['mass']=bounded([-.012])
    elif bad=='invalid_tensor':props['inertia']=bounded(np.diag([1.,-1.,1.]))
    transport=Files(exported);folder=tmp_path/'download';folder.mkdir()
    with pytest.raises(PullError):download(transport,result,folder,URL)
    assert not transport.calls


@pytest.mark.parametrize('bad',['asymmetry','triangle','nonfinite','bounds','layout'])
def test_invalid_native_tensor_or_bounds_are_rejected_before_exports(tmp_path,exported,bad):
    result=manifest();props=result['parts']['coupon']['native_mass_properties']
    if bad=='asymmetry':
        matrix=INERTIA.copy();matrix[0,1]*=2;props['inertia']=bounded(matrix)
    elif bad=='triangle':props['inertia']=bounded(np.diag([1.,1.,3.]))
    elif bad=='nonfinite':props['mass'][0]=float('nan')
    elif bad=='bounds':props['centroid'][3]=props['centroid'][0]+1
    elif bad=='layout':props['inertia']=[1.,2.]
    transport=Files(exported);folder=tmp_path/'download';folder.mkdir()
    with pytest.raises(PullError):download(transport,result,folder,URL)
    assert not transport.calls


def test_mass_read_encodes_part_id_and_preserves_source_configuration():
    rest=Rest();part=rest.definition['parts'][0];instance=rest.definition['rootAssembly']['instances'][0]
    for value in (part,instance):value.update(partId='solid/alternate',configuration='variant=fixture')
    result=assembly(rest,URL)
    call=next(call for call in rest.calls if call[1].endswith('/massproperties'))
    assert call[1]=='/api/v9/parts/d/doc/v/pin/e/studio/partid/solid%2Falternate/massproperties'
    assert call[2]['configuration']=='variant=fixture'
    assert result['definition']['parts'][0]['native_mass_properties']==properties()


def test_a_different_part_mass_response_is_not_used():
    rest=Rest();request=rest.api_request
    def wrong(method,path,query=None):
        if path.endswith('/massproperties'):return {'bodies':{'another-part':properties()}}
        return request(method,path,query)
    rest.api_request=wrong
    with pytest.raises(PullError) as captured:assembly(rest,URL)
    assert captured.value.details['part_id']=='solid'
    assert captured.value.details['occurrence_path']==['instance']
