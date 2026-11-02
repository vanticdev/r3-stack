"""Metadata writes must not resolve an unrelated default model selection."""
import yaml
from r3_catalog.modelbank import ModelBank,ModelEntry
from r3_catalog.schema import ModelRef
from r3_model.project import Project
from r3_orchestrator import catalog_ops
from test_catalog_selection_recovery import isolated_catalog,project,body


def test_explicit_project_selection_survives_ambiguous_platform_metadata_lookup(tmp_path):
    import r3_catalog
    root=project(tmp_path)
    platform=r3_catalog.DATA_DIR/'components'
    platform.mkdir()
    (platform/'fixture.yaml').write_text(yaml.safe_dump({'components':[body()]}))
    bank=ModelBank(dataset=tmp_path/'empty-platform-bank')
    for identifier in ('source-a','source-b'):
        bank.put(ModelEntry(id=identifier,name='Synthetic source fixture',category='other',catalog_component='fixture-body'))
    chosen=body()
    chosen['model']=ModelRef(source='vendor',params={'bank_id':'source-b'}).model_dump(mode='json')
    path=root/'catalog/components/fixture.yaml'
    path.write_text(yaml.safe_dump({'components':[chosen]}))
    assert Project.load(root).catalog.component('fixture-body').model.params['bank_id']=='source-b'
    changed={**chosen,'name':'Updated synthetic metadata'}
    result=catalog_ops.put_entry(root,'component',changed)
    assert result['ok'],result
    assert result['overrides_platform_entry'] is True
    assert Project.load(root).catalog.component('fixture-body').model.params['bank_id']=='source-b'

    # The metadata-only identity query must not waive model choice for a project.
    before = path.read_bytes()
    unselected = catalog_ops.put_entry(root, 'component', body())
    assert not unselected['ok']
    assert unselected['code'] == 'catalog_entry_invalid'
    assert any('ModelSelectionRequired' in error for error in unselected['errors'])
    assert path.read_bytes() == before
