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
    with pytest.raises(StudioBuildError,match='model_features_unverified'):
        purchased.import_studio(ImportAPI(),'doc','workspace',p)


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
