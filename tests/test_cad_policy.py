"""R3's Onshape client must refuse every way of authoring or running FeatureScript."""

import pytest

from r3_onshape.client import ApiKeyTransport
from r3_onshape.policy import FeatureScriptForbidden, check_request


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/featurestudios/d/D/w/W", {"name": "x"}),
    ("POST", "/featurestudios/d/D/w/W/e/E", {"contents": "FeatureScript 3095;"}),
    ("GET", "/featurestudios/d/D/w/W/e/E/featurespecs", None),
    ("POST", "/partstudios/d/D/w/W/e/E/featurescript", {"script": "function(context, queries) { return 1; }"}),
    ("POST", "/partstudios/d/D/w/W/e/E/features", {"feature": {"btType": "BTMFeature-134", "featureType": "r3Robot", "namespace": "eabc::m1"}}),
    ("POST", "/partstudios/d/D/w/W/e/E/features", {"feature": {"btType": "BTMFeature-134", "featureType": "myCustomThing"}}),
    ("POST", "/documents/d/D/w/W/elements", {"elementType": "FEATURESTUDIO", "name": "fs"}),
])
def test_forbidden(method, path, body):
    with pytest.raises(FeatureScriptForbidden):
        check_request(method, path, body)


def test_standard_features_allowed():
    sketch = {"feature": {"btType": "BTMSketch-151", "featureType": "newSketch", "entities": []}}
    extrude = {"feature": {"btType": "BTMFeature-134", "featureType": "extrude", "namespace": "", "parameters": []}}
    check_request("POST", "/partstudios/d/D/w/W/e/E/features", sketch)
    check_request("POST", "/partstudios/d/D/w/W/e/E/features", extrude)
    check_request("GET", "/partstudios/d/D/w/W/e/E/bodydetails")


def test_transport_enforces_policy_before_network():
    api = ApiKeyTransport("k", "s", base="http://127.0.0.1:9")  # unreachable: proves we never send
    with pytest.raises(FeatureScriptForbidden):
        api.request("POST", "/partstudios/d/D/w/W/e/E/featurescript", body={"script": "x"})
