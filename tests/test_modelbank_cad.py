"""Recorded-shape Onshape transport contracts for the purchased path; never live CAD."""
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

from r3_catalog.modelbank import ModelEntry, Source, Spec, Validation
from r3_onshape.geometry import PartPlan
from r3_onshape.parts import ConnectorSpec, LocalPart
from r3_onshape.policy import check_request
from r3_onshape import purchased
from r3_onshape.studio import StudioBuildError


def part():
    entry = ModelEntry(id='vendor-motor', name='Motor', category='motor', sources=[Source(kind='vendor_step', url='https://vendor.test/m.step', sha256='a'*64)],
                       key_specs={'dimensions': Spec(value=[0.01,0.02,0.03],unit='m')},
                       validation=Validation(status='passed', key_features={'mount_hole':True}, date='2026-10-29', validated_by='fixture'),
                       placement={'origin':[0,0,0], 'x':[1,0,0], 'z':[0,0,1]},
                       interface_frames={'mc_mount':{'origin':[0,0,0], 'x':[1,0,0], 'z':[0,0,1]}})
    return LocalPart(key='motor', name='Motor', studio='Motor', type='motor',
                     plan=PartPlan(name='Motor', kind='purchased', material='Vendor assembly', density=0, mass=0.1, color='#888888'),
                     connectors=[ConnectorSpec('mc_mount',np.eye(4),'mount')], bank_model=entry.model_dump())


class ImportAPI:
    def __init__(self):
        self.calls=[]
    def upload(self,path,file,fields):
        self.calls.append(('UPLOAD',path,fields))
        return {'requestState':'DONE','resultElementIds':['imported-studio']}
    def json(self, method, path, body=None, query=None):
        check_request(method,path,body)
        self.calls.append((method,path,body))
        if path.startswith('/parts/'):
            return [{'partId':'real-vendor-body','name':'Imported rigid composite'}]
        if path.endswith('/boundingboxes'):
            return {'lowX':0,'lowY':0,'lowZ':0,'highX':0.01,'highY':0.02,'highZ':0.03}
        if method=='GET' and path.startswith('/metadata/'):
            return {'properties':[]}
        if path.endswith('/features'):
            return {'feature':{'featureId':'mc-real'},'featureState':{'featureStatus':'OK'}}
        return {}


def test_import_uses_real_body_and_mass_not_envelope(tmp_path,monkeypatch):
    step=tmp_path/'part.step';step.write_text('ISO-10303-21;')
    monkeypatch.setattr(purchased,'acquire',lambda s:(step,s))
    api=ImportAPI(); built=purchased.import_studio(api,'doc','workspace',part())
    assert built.part_ids['motor']=='real-vendor-body'
    assert built.connectors['motor']['mc_mount']=='mc-real'
    features=[body['feature']['featureType'] for method,path,body in api.calls if method=='POST' and path.endswith('/features')]
    assert features==['mateConnector']
    assert api.calls[0][2]['createComposite'] is True
    assert api.calls[0][2]['storeInDocument'] is True
    assert 'formatName' not in api.calls[0][2]  # default target is native ONSHAPE, not an exported STEP blob
    mass=[body for method,path,body in api.calls if method=='POST' and path.startswith('/metadata/')][0]
    assert any(prop['value']=='0.1 kg' for prop in mass['properties'])


def test_unreviewed_interface_never_becomes_a_mate(tmp_path,monkeypatch):
    step=tmp_path/'part.step';step.write_text('ISO-10303-21;')
    monkeypatch.setattr(purchased,'acquire',lambda s:(step,s))
    p=part();p.bank_model['interface_frames']={}
    api=ImportAPI()
    with pytest.raises(StudioBuildError,match='model_features_unverified'):
        purchased.import_studio(api,'doc','workspace',p)
    assert api.calls==[]


@pytest.mark.parametrize('case,code', [('missing','model_features_unverified'),
                                     ('mismatch','model_datasheet_mismatch'),
                                     ('rotation','model_frame_unverified'),
                                     ('validation','model_features_unverified')])
def test_import_prerequisites_report_and_reject_before_upload(monkeypatch,case,code):
    p=part()
    if case=='missing': p.bank_model['interface_frames']={}
    if case=='mismatch': p.bank_model['interface_frames']['mc_mount']['origin']=[1,0,0]
    if case=='rotation': p.bank_model['placement']['x']=[0,1,0]
    if case=='validation': p.bank_model['validation']['status']='unverified'
    monkeypatch.setattr(purchased,'acquire',lambda *_:pytest.fail('acquired an invalid import'))
    blockers=purchased.import_blockers(p)
    assert blockers[0]['code']==code
    if case=='missing': assert blockers[-1]['missing_connectors']==['mc_mount']
    if case=='mismatch': assert blockers[-1]['mismatched_connectors']==['mc_mount']
    api=ImportAPI()
    with pytest.raises(StudioBuildError,match=code):
        purchased.import_studio(api,'doc','workspace',p)
    assert api.calls==[]


def test_bank_interface_name_resolves_without_inventing_hole_seat_evidence():
    p=part()
    p.bank_model['interface_frames']['mount']=p.bank_model['interface_frames'].pop('mc_mount')
    assert purchased.import_blockers(p)==[]
    p.connectors.append(ConnectorSpec('mc_mount_h1',np.eye(4),'mount',hole=0))
    blockers=purchased.import_blockers(p)
    assert blockers==[{'code':'model_features_unverified','missing_connectors':['mc_mount_h1'],
                       'reason':'missing geometry-derived frames: mc_mount_h1'}]
    p.bank_model['interface_frames']['mount']['origin']=[1,0,0]
    assert purchased.import_blockers(p)[-1]['mismatched_connectors']==['mc_mount']


def test_public_insert_uses_immutable_version():
    class API:
        def __init__(self): self.inserted=False; self.calls=[]
        def json(self,method,path,body=None):
            check_request(method,path,body);self.calls.append((method,path,body))
            if path=='/documents/public': return {'isPublic':True}
            if path.endswith('/instances'):
                self.inserted=True;return {}
            if method=='GET': return {'rootAssembly':{'instances':[{'id':'instance'}] if self.inserted else []}}
            return {}
    entry=ModelEntry(id='public',name='Public model',category='motor',sources=[Source(kind='onshape_public',url='https://cad.onshape.com/documents/public/v/version/e/studio',did='public',vid='version',eid='studio',part_id='part')])
    api=API();report=purchased.insert_public(api,'target','work','assembly',entry,np.eye(4))
    body=next(b for m,p,b in api.calls if p.endswith('/instances'))
    assert body['versionId']=='version' and body['partId']=='part'
    assert report['instances']==['instance']
    assert not any('/features' in p for m,p,b in api.calls)


def test_diagnostics_distinguish_missing_and_mismatched(tmp_path):
    from r3_catalog.modelbank import ModelBank
    from r3_model.diagnostics import StageResult
    bank=ModelBank(tmp_path/'bank',tmp_path/'index')
    entry=part().bank_model;entry['catalog_component']='motor';entry['validation']['status']='mismatch'
    bank.put(ModelEntry.model_validate(entry))
    component=SimpleNamespace(id='motor',model=None,kind='motor',mechanical=SimpleNamespace(fabricated=False))
    project=SimpleNamespace(spec=SimpleNamespace(components={'m':SimpleNamespace(type='motor')}),catalog=SimpleNamespace(component=lambda _id:component))
    result=StageResult(stage='cad');purchased.diagnostics(project,result,bank)
    assert result.diagnostics[0].code=='model_datasheet_mismatch'
    assert result.diagnostics[0].data['model_bank_id']=='vendor-motor'


def test_translated_import_includes_every_native_transform_default(tmp_path, monkeypatch):
    import json
    defaults = json.loads((Path(__file__).parent / 'fixtures/onshape/transform-defaults.json').read_text())
    expected = {p['parameterId'] for p in defaults}

    class NativeTransformAPI(ImportAPI):
        def json(self, method, path, body=None, query=None):
            if method == 'POST' and path.endswith('/features') and body['feature']['featureType'] == 'transform':
                params = {p['parameterId']: p for p in body['feature']['parameters']}
                assert set(params) == expected
                assert params['makeCopy']['value'] is False
                assert params['entities']['queries'][0]['deterministicIds'] == ['real-vendor-body']
                assert params['dx']['expression'] == '1.000000 mm'
                assert params['dy']['expression'] == '-2.000000 mm'
                assert params['dz']['expression'] == '3.000000 mm'
            return super().json(method, path, body=body, query=query)

    step = tmp_path / 'synthetic.step'
    step.write_text('ISO-10303-21; synthetic transport fixture')
    monkeypatch.setattr(purchased, 'acquire', lambda source: (step, source))
    p = part()
    p.bank_model['placement']['origin'] = [.001, -.002, .003]
    api = NativeTransformAPI()
    built = purchased.import_studio(api, 'doc', 'workspace', p)
    assert built.part_ids[p.key] == 'real-vendor-body'
    assert [body['feature']['featureType'] for method, path, body in api.calls if method == 'POST' and path.endswith('/features')] == ['transform', 'mateConnector']
