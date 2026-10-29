from pathlib import Path

import numpy as np

from r3_model.project import Project
from r3_onshape.featurescript import feature_studio_source, generate, source_digest
from r3_onshape.sync import expected_bbox, verify_contacts
from r3_model.diagnostics import StageResult

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


def test_one_named_part_per_component_and_deterministic():
    project = Project.load(RC_CAR)
    source = feature_studio_source(project, "3095")
    for cid in project.spec.components:
        assert f'"value" : "{cid}"' in source
    assert source_digest(project) == source_digest(Project.load(RC_CAR))


def test_purchased_parts_use_mass_matched_density():
    _, meta = generate(Project.load(RC_CAR))
    assert meta["chassis"]["fabricated"] and not meta["battery"]["fabricated"]


def test_expected_bbox_rotated_fuse():
    lo, hi = expected_bbox(Project.load(RC_CAR), "fuse")
    assert np.allclose(hi - lo, [0.012, 0.030, 0.022], atol=1e-9)


def test_contact_graph_flags_floating_and_interference():
    project = Project.load(RC_CAR)
    result = StageResult(stage="cad")
    verify_contacts(project, [("chassis", "battery", 0.0, 2e-6)], result)
    codes = {d.code for d in result.diagnostics}
    assert "cad_interference" in codes and "cad_part_floating" in codes
