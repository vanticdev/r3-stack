"""Estimated owned-part mounts must remain visible to the release gate."""
import yaml

from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_orchestrator.model_stage import ModelChecker


def test_owned_sensor_mount_estimate_is_reported_until_measured(tmp_path):
    directory = tmp_path / "catalog" / "components"
    directory.mkdir(parents=True)
    (directory / "sensor.yaml").write_text(yaml.safe_dump({"components": [{
        "id": "spectrometer", "kind": "sensor", "name": "Owned spectrometer",
        "mechanical": {"shape": "box", "size": [.03, .02, .004], "mass": .01,
                       "features": [{"id": "mount", "kind": "clearance_hole", "position": [.01, 0, .002],
                                     "diameter": .0022, "depth": .002,
                                     "provenance": {"status": "estimated", "source": "Unconfirmed supplier drawing"}}]}
    }]}))
    (tmp_path / "robot.yaml").write_text(yaml.safe_dump({
        "schema": "r3/robot@1", "robot": {"name": "spectrometer fixture"},
        "components": {"sensor": {"type": "spectrometer", "owned": True}}
    }))
    project = Project.load(tmp_path)
    result = StageResult(stage="model")
    ModelChecker(project, result).check_mount_estimates()
    assert len(result.diagnostics) == 1
    warning = result.diagnostics[0]
    assert warning.code == "mount_geometry_estimated"  # release stage evidence recognizes this suffix
    assert warning.subjects == ["sensor"] and warning.data["holes"][0]["diameter"] == .0022
    project.catalog.component("spectrometer").mechanical.features[0].provenance.status = "measured"
    measured = StageResult(stage="model")
    ModelChecker(project, measured).check_mount_estimates()
    assert not measured.diagnostics
