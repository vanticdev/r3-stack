"""R3's Onshape client must refuse every way of authoring or running FeatureScript."""

import pytest

from r3_onshape.client import ApiKeyTransport
from r3_onshape.policy import FeatureScriptForbidden, check_request


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/featurestudios/d/D/w/W", {"name": "x"}),
    ("POST", "/featurestudios/d/D/w/W/e/E", {"contents": "FeatureScript 3095;"}),
    ("GET", "/featurestudios/d/D/w/W/e/E/featurespecs", None),
    ("GET", "/documents/d/D/w/W/e/E/featurespecs", None),
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


def test_builtin_composite_allowed_but_custom_namespace_still_refused():
    feature = {"btType": "BTMFeature-134", "featureType": "compositePart", "namespace": "",
               "parameters": [{"btType": "BTMParameterBoolean-144", "parameterId": "closed", "value": True}]}
    check_request("POST", "/api/v9/partstudios/d/D/w/W/e/E/features", {"feature": feature})
    with pytest.raises(FeatureScriptForbidden):
        check_request("POST", "/api/v9/partstudios/d/D/w/W/e/E/features", {"feature": {**feature, "namespace": "custom::m1"}})


def test_transport_enforces_policy_before_network():
    api = ApiKeyTransport("k", "s", base="http://127.0.0.1:9")  # unreachable: proves we never send
    with pytest.raises(FeatureScriptForbidden):
        api.request("POST", "/partstudios/d/D/w/W/e/E/featurescript", body={"script": "x"})


def test_builtin_feature_specs_and_session_endpoints_allowed():
    """Reading standard feature specs, logging in headlessly, rendering shaded
    views and setting mate values author no FeatureScript."""
    check_request("GET", "/partstudios/d/D/w/W/e/E/featurespecs")
    check_request("GET", "/assemblies/d/D/w/W/e/E/featurespecs")
    check_request("POST", "/users/session", {"email": "a@b.c", "password": "x"})
    check_request("GET", "/assemblies/d/D/w/W/e/E/shadedviews", None)
    check_request("POST", "/assemblies/d/D/w/W/e/E/matevalues", {"mateValues": []})
    mate = {"feature": {"btType": "BTMMate-64", "featureType": "mate", "namespace": "", "parameters": []}}
    check_request("POST", "/assemblies/d/D/w/W/e/E/features", mate)


def test_session_transport_enforces_policy_before_network(tmp_path):
    from r3_onshape.session import SessionTransport

    api = SessionTransport(base="http://127.0.0.1:9", session_file=tmp_path / "s.json")
    with pytest.raises(FeatureScriptForbidden):
        api.request("POST", "/partstudios/d/D/w/W/e/E/featurescript", body={"script": "x"})
    with pytest.raises(FeatureScriptForbidden):
        api.request("POST", "/featurestudios/d/D/w/W", body={"name": "fs"})
