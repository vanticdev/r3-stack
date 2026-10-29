"""The voxel scikit-fem model against closed-form beam theory, and the Z-strength rule."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from r3_fab import strength

L, W, T = 60.0, 10.0, 3.0  # mm
E = 2000e6  # Pa
I = W * T ** 3 / 12  # mm^4


def cantilever(pitch: float, tip_load_n: float = 0.0, density: float = 0.0, accel_g: float = -1.0) -> strength.CaseResult:
    box = trimesh.creation.box([L, W, T])
    box.apply_translation([L / 2, W / 2, T / 2])
    model = strength.voxel_model(box, pitch)
    system = strength.fe_system(model)
    clamp = np.nonzero(np.isclose(system.points[0], 0.0))[0]
    tip = np.nonzero(np.isclose(system.points[0], L))[0]
    loads = [(tip, tip_load_n / strength.G0)] if tip_load_n else []
    return strength.solve(model, system, E, 0.38, density, clamp, loads, [{"id": "g", "accel": [0, 0, accel_g]}], np.eye(3))[0]


@pytest.mark.parametrize("pitch", [3.0, 1.0])
def test_tip_loaded_cantilever_matches_euler_bernoulli(pitch):
    result = cantilever(pitch, tip_load_n=1.0)
    deflection = 1.0 * L ** 3 / (3 * E / 1e6 * I)  # P L^3 / (3 E I) = 1.6 mm
    assert result.max_deflection_mm == pytest.approx(deflection, rel=0.05)
    root = 1.0 * L * (T / 2) / I  # M c / I = 4.0 MPa at the clamp
    assert result.max_von_mises_mpa == pytest.approx(root, rel=0.1)


def test_self_weight_cantilever_matches_beam_theory():
    density = 1e6  # kg/m^3, large so the deflection is well above round-off
    result = cantilever(1.5, density=density)
    q = density * 1e-9 * strength.G0 * W * T  # N/mm
    assert result.max_deflection_mm == pytest.approx(q * L ** 4 / (8 * E / 1e6 * I), rel=0.05)


def test_load_sign_does_not_change_von_mises():
    up, down = cantilever(3.0, tip_load_n=1.0, accel_g=1.0), cantilever(3.0, tip_load_n=1.0, accel_g=-1.0)
    assert up.max_von_mises_mpa == pytest.approx(down.max_von_mises_mpa, rel=1e-9)


def test_z_strength_applies_when_the_stress_runs_across_layers():
    result = cantilever(3.0, tip_load_n=1.0)
    # Bending stress runs along x: printed with x up the layers carry it in Z (interlayer) strength.
    sf_flat, across_flat = strength.safety_factor(result, 34e6, 23e6, np.array([0.0, 0.0, 1.0]))
    sf_upright, across_upright = strength.safety_factor(result, 34e6, 23e6, np.array([1.0, 0.0, 0.0]))
    assert not across_flat and across_upright
    assert sf_flat == pytest.approx(34.0 / result.max_von_mises_mpa, rel=1e-6)
    assert sf_upright == pytest.approx(23.0 / result.max_von_mises_mpa, rel=1e-6)
