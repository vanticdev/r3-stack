"""Invalid authoring stops physics with actionable generic diagnostics."""
from types import SimpleNamespace
from r3_sim.electrical import stage


def test_unknown_gauge_never_starts_partial_electrical_simulation(tmp_path, monkeypatch):
    project = SimpleNamespace(fingerprint=lambda: {}, build_dir=lambda name: tmp_path,
        read_build_json=lambda path: None, catalog=SimpleNamespace(wires={12:object()}),
        spec=SimpleNamespace(electrical=SimpleNamespace(harnesses=[SimpleNamespace(id='fixture',
            wires=[SimpleNamespace(id='lead', awg=99)])])))
    def forbidden(*args, **kwargs): raise AssertionError('Invalid inputs must not reach physics')
    monkeypatch.setattr(stage,'ElectricalSystem',forbidden)
    result=stage.run(project)
    assert not result.ok and len(result.errors)==1
    assert result.errors[0].code=='wire_gauge_unknown' and result.errors[0].data=={'awg':99}
    assert 'catalog/wires.yaml' in result.errors[0].message
