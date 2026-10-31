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


def test_missing_project_factory_is_actionable_stage_error(tmp_path, monkeypatch):
    from r3_sim.project_devices import electrical_factory
    project = SimpleNamespace(fingerprint=lambda: {}, build_dir=lambda name: tmp_path,
        read_build_json=lambda path: None, catalog=SimpleNamespace(wires={}),
        spec=SimpleNamespace(electrical=SimpleNamespace(harnesses=[]),
                             simulation=SimpleNamespace(devices={})))
    monkeypatch.setattr(stage, 'ElectricalSystem', lambda *args: electrical_factory(project, 'probe', 'project.unbound'))
    result = stage.run(project)
    assert not result.ok and result.errors[0].code == 'project_device_configuration_invalid'
    assert 'electrical_factory' in result.errors[0].message
    assert 'simulation.devices' in result.errors[0].hint


def test_project_factory_must_be_callable(monkeypatch):
    import pytest
    from r3_sim import project_devices as devices
    definition = SimpleNamespace(component='probe', source='simulation/probe.py', electrical_factory='make')
    project = SimpleNamespace(spec=SimpleNamespace(simulation=SimpleNamespace(devices={'probe': definition})))
    monkeypatch.setattr(devices, 'load_module', lambda *args: SimpleNamespace(make=42))
    with pytest.raises(devices.ProjectDeviceConfigurationError, match='callable make'):
        devices.electrical_factory(project, 'probe', 'project.probe')
