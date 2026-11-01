"""Native source revision identity, with unrelated synthetic part records."""
import copy
import numpy as np
import pytest
from r3_model.pulled import PullError
from r3_onshape.pull import describe,target
from r3_onshape.rest_snapshot import assembly

URL="https://cad.onshape.com/documents/fixture/v/pin/e/assembly"

def data():
    parts=[];instances=[];occurrences=[];rows=[]
    for i in (1,2):
        reference={"documentId":"source","elementId":"studio","partId":"body",
                   "documentMicroversion":f"micro{i}","documentVersion":f"pin{i}"}
        record={**reference,"material":{"name":f"Synthetic{i}","density":1000*i},
                "mateConnectors":[{"featureId":"port","mateConnectorCS":{
                    "origin":[i/16,0,0],"xAxis":[1,0,0],"zAxis":[0,0,1]}}]}
        parts.append(record)
        instances.append({**reference,"id":f"instance{i}","name":f"coupon{i}","type":"Part"})
        occurrences.append({"path":[f"instance{i}"],"transform":np.eye(4).ravel().tolist()})
        rows.append({"kind":"occurrence","instance_type":"Part","occurrence_path":[f"instance{i}"],
                     "name":f"coupon{i}","reference":reference,"transform":np.eye(4).ravel().tolist()})
    definition={"parts":parts,"subAssemblies":[],"rootAssembly":{
        "documentId":"fixture","documentVersion":"pin","elementId":"assembly",
        "instances":instances,"occurrences":occurrences,"features":[]}}
    return {"definition":definition,"rows":rows,"microversion":"fixture-micro"}

@pytest.mark.parametrize("reverse",[False,True])
def test_each_source_revision_retains_its_connectors_and_material(reverse):
    snapshot=data()
    if reverse:snapshot["definition"]["parts"].reverse()
    result=describe(snapshot,target(URL),{})
    for i in (1,2):
        part=result["parts"][f"coupon{i}"]
        assert part["density"]==1000*i
        assert part["mate_connectors"]["port"][0][3]==i/16
        assert part["onshape"]["version_id"]==f"pin{i}"

class Rest:
    def __init__(self):self.calls=[];self.snapshot=data()
    def api_request(self,method,path,query=None):
        self.calls.append((method,path));assert method=="GET" and "/features" not in path
        if "/versions/" in path:return {"microversion":"fixture-micro"}
        if path.endswith('/massproperties'):
            from native_mass_fixture import uniform_box
            i=1 if "/v/pin1/" in path else 2
            return {'bodies': {'body': uniform_box(1000*i)}}
        if "/parts/" in path:
            i=1 if "/v/pin1/" in path else 2
            return [{"partId":"body","name":f"coupon{i}","material":{"name":f"Synthetic{i}","density":1000*i}}]
        return copy.deepcopy(self.snapshot["definition"])

def test_rest_snapshot_preserves_separate_native_source_revisions():
    rest=Rest();result=describe(assembly(rest,URL),target(URL),{})
    assert result["parts"]["coupon1"]["density"]==1000
    assert result["parts"]["coupon2"]["density"]==2000
    assert result["parts"]["coupon1"]["native_mass_properties"]["mass"][0]==pytest.approx(.006)
    assert result["parts"]["coupon2"]["native_mass_properties"]["mass"][0]==pytest.approx(.012)
    assert len(rest.calls)==6

def test_unknown_source_revision_is_never_replaced_by_another():
    snapshot=data();snapshot["rows"][0]["reference"]["documentMicroversion"]="missing-micro"
    with pytest.raises(PullError):describe(snapshot,target(URL),{})


def test_named_versions_disambiguate_records_without_microversions():
    snapshot=data()
    for record in snapshot['definition']['parts']:
        record.pop('documentMicroversion')
    for row in snapshot['rows']:
        row['reference'].pop('documentMicroversion')
    result=describe(snapshot,target(URL),{})
    assert result['parts']['coupon1']['density']==1000
    assert result['parts']['coupon2']['density']==2000


def test_missing_source_identity_never_selects_an_arbitrary_revision():
    snapshot=data()
    reference=snapshot['rows'][0]['reference']
    reference.pop('documentMicroversion');reference.pop('documentVersion')
    with pytest.raises(PullError) as captured:describe(snapshot,target(URL),{})
    assert captured.value.code=='cad_pull_incomplete'
    assert captured.value.details['matching_records']==2


def test_configuration_remains_part_of_source_identity():
    snapshot=data()
    for i in range(2):
        record=snapshot['definition']['parts'][i]
        reference=snapshot['rows'][i]['reference']
        for value in (record,reference):
            value.update(documentMicroversion='same-micro',documentVersion='same-pin',configuration=f'option={i}')
    result=describe(snapshot,target(URL),{})
    assert result['parts']['coupon1']['density']==1000
    assert result['parts']['coupon2']['density']==2000


def test_identical_microversion_can_have_another_named_version_alias():
    snapshot=data()
    snapshot['rows'][0]['reference']['documentVersion']='alias-pin'
    result=describe(snapshot,target(URL),{})
    assert result['parts']['coupon1']['density']==1000
    assert result['parts']['coupon1']['onshape']['version_id']=='alias-pin'
