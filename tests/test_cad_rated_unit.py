"""Factory rating evidence is narrow; external geometry checks are unchanged."""
from types import SimpleNamespace

import numpy as np
import pytest

from r3_catalog.modelbank.schema import ModelEntry, Source, Validation
from r3_catalog.schema import Mechanical, RatedInternalJoint
from r3_model.diagnostics import StageResult
from r3_model.ratings import RatedJointDemand
from r3_model.spec import ComponentInstance, Interface, Join
from r3_onshape.fit import FitChecker
from r3_onshape.model import GeometryModel
from r3_onshape.step_geometry import geometry_from_pulled
from r3_model.pulled import load_pulled
from pulled_fixture import write_pull
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox


@pytest.fixture
def unit(monkeypatch, tmp_path):
    rating = RatedInternalJoint(motion='rotational', interface='bearing', member_type='supplied-rotor',
        member_interface='journal', ratings={'torque': 2., 'angular_speed': 5.}, source='https://manufacturer.example/unit.pdf')
    request = RatedJointDemand(unit='drive', joint='output', demand={'torque': 1., 'angular_speed': 3.},
                               source='Design load case: payload acceleration and required traverse time')
    catalog = {name: SimpleNamespace(id=tid, mechanical=Mechanical(size=[.02]*3, mass=.1,
                    rated_internal_joints={'output': rating} if name == 'drive' else {}))
               for name, tid in [('drive', 'supplied-unit'), ('rotor', 'supplied-rotor')]}
    by_type = {c.id: c for c in catalog.values()}
    components = {'drive': ComponentInstance(type='supplied-unit'),
                  'rotor': ComponentInstance(type='supplied-rotor', purchased_unit='drive')}
    join = Join(id='axis', a='drive.bearing', b='rotor.journal', kind='revolute', rated_internal=request)
    interface = Interface(id='axis', kind='revolute_fit', parts=['rotor', 'drive'],
                          axis_point=[0, 0, 0], axis_dir=[0, 0, 1], join='axis', rated_internal=request,
                          params={'clearance': [0., .0001], 'min_engagement': .005})
    entries = {c.id: ModelEntry(id=c.id, name=c.id, category='other',
                sources=[Source(kind='vendor_step', url='https://manufacturer.example/unit.step',
                                sha256='a'*64, solid_indices=[i])],
                placement={'origin': [0, 0, 0], 'x': [1, 0, 0], 'z': [0, 0, 1]},
                validation=Validation(status='passed')) for i, c in enumerate(catalog.values())}
    monkeypatch.setattr('r3_onshape.rated_units.bank_entry', lambda c, project=None: entries[c.id])
    write_pull(tmp_path, {name: BRepPrimAPI_MakeBox(20, 20, 20).Shape() for name in components})
    model = geometry_from_pulled(load_pulled(tmp_path))
    project = SimpleNamespace(spec=SimpleNamespace(components=components, assembly=SimpleNamespace(joins=[join])),
                              catalog=SimpleNamespace(component=by_type.__getitem__),
                              assembly=SimpleNamespace(poses={name: np.eye(4) for name in components}))
    result = StageResult(stage='cad')
    checker = FitChecker(project, model, {}, result)
    return checker, interface, rating, entries, catalog


def test_rated_internal_joint_verifies_and_reports_overlap(unit):
    checker, interface, _, _, _ = unit
    checker.check_interface(interface)
    checker.check_interference()
    assert not checker.result.errors
    assert 'axis' in checker.report.verified_interfaces
    evidence = next(d for d in checker.result.diagnostics if d.code == 'rated_internal_joint_verified')
    assert evidence.severity.value == 'info'
    assert evidence.data['overlap_volume_m3'] == pytest.approx(.02**3, rel=1e-5)
    assert evidence.data['rating_source'].startswith('https://manufacturer.example/')
    assert checker.report.interfaces['axis']['ratings'] == {'torque': 2., 'angular_speed': 5.}
    assert not checker.report.links[0].rigid


@pytest.mark.parametrize('missing', ['declaration', 'torque', 'angular_speed', 'source'])
def test_incomplete_manufacturer_rating_is_an_error(unit, missing):
    checker, interface, rating, _, catalog = unit
    if missing == 'declaration':
        catalog['drive'].mechanical.rated_internal_joints.clear()
    elif missing == 'source':
        rating.source = ''
    else:
        rating.ratings.pop(missing)
    checker.check_interface(interface)
    assert 'rated_joint_rating_missing' in {d.code for d in checker.result.errors}
    assert not checker.report.verified_interfaces
    assert not checker.report.allowed


@pytest.mark.parametrize('metric,value', [('torque', 2.01), ('angular_speed', 5.01)])
def test_demand_above_rating_fails(unit, metric, value):
    checker, interface, *_ = unit
    interface.rated_internal.demand[metric] = value
    checker.check_interface(interface)
    assert 'rated_joint_overload' in {d.code for d in checker.result.errors}
    assert not checker.report.verified_interfaces


def test_missing_design_demand_fails(unit):
    checker, interface, *_ = unit
    interface.rated_internal.demand.pop('torque')
    checker.check_interface(interface)
    assert 'rated_joint_demand_missing' in {d.code for d in checker.result.errors}


def test_printed_member_gets_normal_geometric_fit_check(unit):
    checker, interface, _, _, catalog = unit
    catalog['rotor'].mechanical.fabricated = True
    checker.check_interface(interface)
    codes = {d.code for d in checker.result.errors}
    assert {'rated_joint_scope_invalid', 'interface_geometry_missing', 'interface_fit_out_of_range'} <= codes
    assert not checker.report.allowed


@pytest.mark.parametrize('change', ['unit', 'asset', 'solids', 'pose'])
def test_two_separate_parts_cannot_be_relabelled_as_one_unit(unit, change):
    checker, interface, _, entries, _ = unit
    if change == 'unit':
        checker.spec.components['rotor'].purchased_unit = None
    elif change == 'asset':
        entries['supplied-rotor'].sources[0].sha256 = 'b'*64
    elif change == 'solids':
        entries['supplied-rotor'].sources[0].solid_indices = [0]
    else:
        checker.project.assembly.poses['rotor'][0, 3] = .01
    checker.check_interface(interface)
    assert checker.result.errors and not checker.report.allowed


def test_external_interference_is_still_an_error(unit):
    checker, interface, *_ = unit
    checker.model.parts['external'] = checker.model['drive']
    checker._boxes['external'] = checker._boxes['drive']
    checker.check_interface(interface)
    checker.check_interference()
    errors = [d for d in checker.result.errors if d.code == 'cad_interference']
    assert len(errors) == 2
    assert all('external' in d.subjects for d in errors)


def test_translational_unit_checks_force_and_linear_speed(unit):
    checker, interface, rating, *_ = unit
    rating.motion = 'translational'
    rating.ratings = {'force': 100., 'linear_speed': .2}
    checker.spec.assembly.joins[0].kind = 'slider'
    interface.rated_internal.demand = {'force': 80., 'linear_speed': .1}
    checker.check_interface(interface)
    assert not checker.result.errors
    assert 'axis' in checker.report.verified_interfaces


def test_rating_cannot_be_attached_to_an_external_fastening_join():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match='revolute or slider'):
        Join(a='a.port', b='b.port', rated_internal={'unit': 'a', 'joint': 'axis'})


def test_assembly_derivation_preserves_typed_demand(unit):
    from r3_model.mechanics import _shaft_bore_interface
    checker, _, *_ = unit
    join = checker.spec.assembly.joins[0]
    def endpoint(obj, kind):
        return SimpleNamespace(obj=obj, kind=kind, params={}, frame=np.eye(4), quantity=lambda key, default=None: default)
    result = SimpleNamespace(poses=checker.project.assembly.poses)
    derived = _shaft_bore_interface(join, endpoint('drive', 'bore'), endpoint('rotor', 'shaft'), result)
    assert derived.rated_internal == join.rated_internal
