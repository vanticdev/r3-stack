"""Only a complete verified native pull can retire protected local descriptors."""
from copy import deepcopy
import json

import pytest
import yaml
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

from r3_model.project import Project
from r3_model.pulled import load_pulled
from r3_model.spec_files import load_spec_files
from r3_onshape.pull import pull
from r3_orchestrator.spec_ops import get_spec,patch_spec
from pulled_fixture import write_pull
from test_cad_pulled_consumers import project
from test_cad_pull import native_snapshot
from test_native_mesh_prototype import NativeViewer
from test_native_viewer_capture import URL


def prepare(project):
    path=project.root/'robot.yaml';data=yaml.safe_load(path.read_text())
    data['components']['retired']={'$include':'parts/retired.yaml'}
    (project.root/'parts').mkdir()
    (project.root/'parts/retired.yaml').write_text('type: synthetic-coupon\nlabel: Preserve retired description\n')
    path.write_text(yaml.safe_dump(data))
    write_pull(project.root,{name:BRepPrimAPI_MakeBox(10,20,30).Shape() for name in ('coupon','retired')},
               mates=[{'id':'old-link','kind':'fastened','a':['coupon','origin'],'b':['retired','origin']}])
    return Project.load(project.root)


def snapshot(project):
    files=[project.root/'robot.yaml',project.root/'r3.lock.yaml',*project.root.glob('parts/*.yaml'),
           *(project.root/'cad/pulled').rglob('*')]
    return {str(p.relative_to(project.root)):p.read_bytes() for p in files if p.is_file()}


def test_retirement_is_owned_by_complete_native_pull_not_a_spec_patch(project,native_snapshot):
    project=prepare(project)
    before=get_spec(project.root)
    refused=patch_spec(project.root,{'components':{'retired':None}},before['sha256'])
    assert refused['code']=='spec_read_only'
    orphan=(project.root/'parts/retired.yaml').read_bytes()
    native=NativeViewer(native_snapshot)
    result=pull(project,url=URL,version_id='version',transport=native,mesh_source='viewer',
                remove_components=['retired'])
    assert result['ok'],result
    assert result['removed_components']==['retired']
    assert set(load_pulled(project.root).parts)=={'coupon'}
    assert set(Project.load(project.root).spec.components)=={'coupon'}
    assert (project.root/'parts/retired.yaml').read_bytes()==orphan
    assert 'parts/retired.yaml' not in load_spec_files(project.root).texts
    assert get_spec(project.root)['spec']['attachments']==[]
    assert native.exports==0


def test_retirement_cannot_omit_a_native_occurrence(project,native_snapshot):
    project=prepare(project);before=snapshot(project)
    result=pull(project,url=URL,version_id='version',transport=NativeViewer(native_snapshot),
                mesh_source='viewer',remove_components=['coupon'])
    assert not result['ok']
    assert result['code'] in ('cad_pull_remove_present','cad_pull_selection_required','cad_pull_selection_invalid')
    assert snapshot(project)==before


@pytest.mark.parametrize('reference',['fabrication','native_fastener'])
def test_retirement_does_not_delete_authored_references_to_a_part(project,native_snapshot,reference):
    project=prepare(project)
    read=get_spec(project.root)
    patch = {'fabrication':{'parts':{'retired':{'material':'PLA'}}}} if reference=='fabrication' else {
        'mechanical':{'fasteners':[{'id':'fixture','type':'shcs-m3','body':'retired',
                                   'mate_connector':'origin','clamps':['coupon']}]}}
    changed=patch_spec(project.root,patch,read['sha256'])
    assert changed['ok'],changed
    project=Project.load(project.root);before=snapshot(project)
    result=pull(project,url=URL,version_id='version',transport=NativeViewer(native_snapshot),
                mesh_source='viewer',remove_components=['retired'])
    assert not result['ok']
    assert snapshot(project)==before


def test_retirement_conflict_preserves_concurrent_authored_edits(project,native_snapshot):
    project=prepare(project)
    before=snapshot(project);native=NativeViewer(native_snapshot)
    original=native.artifact_bytes;edited=False
    def download(*args,**kwargs):
        nonlocal edited
        if not edited:
            edited=True
            p=project.root/'robot.yaml';data=yaml.safe_load(p.read_text())
            data['robot']['description']='Concurrent authored change'
            p.write_text(yaml.safe_dump(data))
        return original(*args,**kwargs)
    native.artifact_bytes=download
    result=pull(project,url=URL,version_id='version',transport=native,
                mesh_source='viewer',remove_components=['retired'])
    assert not result['ok'] and result['code']=='spec_conflict',result
    assert get_spec(project.root)['spec']['robot']['description']=='Concurrent authored change'
    after=snapshot(project)
    assert {k:v for k,v in after.items() if k!='robot.yaml'}=={k:v for k,v in before.items() if k!='robot.yaml'}
    assert set(load_pulled(project.root).parts)=={'coupon','retired'}


def test_retirement_is_exposed_through_agent_and_cli_tools(project,monkeypatch):
    from r3_onshape import pull as native_module
    from r3_orchestrator import mcp_server,cli
    calls=[]
    def operation(*args,**kwargs):
        calls.append(kwargs)
        return {'ok':True}
    monkeypatch.setattr(native_module,'pull',operation)
    monkeypatch.setattr(mcp_server.api,'load_project',lambda _:project)
    result=mcp_server.r3_cad_pull(project=str(project.root),url=URL,version_id='version',
                                mesh_source='viewer',remove_components=['retired'])
    assert result['ok'] and calls[-1]['remove_components']==['retired']
    args=cli.build_parser().parse_args(['cad','pull','--url',URL,'--version','version',
                                      '--mesh-source','viewer','--remove-component','retired'])
    assert cli.cmd_cad_pull(args)['ok']
    assert calls[-1]['remove_components']==['retired']


@pytest.mark.parametrize('names',[['absent'],['retired','retired'],'retired'])
def test_invalid_retirement_requests_make_no_native_call(project,native_snapshot,names):
    project=prepare(project);before=snapshot(project)
    native=NativeViewer(native_snapshot)
    def forbidden(*args):
        raise AssertionError('Invalid retirement contacted native CAD')
    native.assembly=forbidden
    result=pull(project,url=URL,version_id='version',transport=native,
                mesh_source='viewer',remove_components=names)
    assert not result['ok'] and result['code']=='cad_pull_invalid'
    assert snapshot(project)==before
