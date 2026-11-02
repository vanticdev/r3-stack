import math

import pytest

from r3_kicad.circuit import analyze_circuit


def element(id, kind, positive, negative, value, **options):
    return dict(id=id, kind=kind, positive=positive, negative=negative, value=value, **options)


def test_divider_and_signed_source_current_match_analytical_solution():
    result = analyze_circuit({'elements': [element('V1', 'V', 'vin', '0', 10),
        element('R1', 'R', 'vin', 'out', 1000), element('R2', 'R', 'out', '0', 1000)]})
    assert result['ok'], result
    assert result['node_voltage_v']['out'] == pytest.approx([5])
    assert result['branch_current_a']['V1'] == pytest.approx([-.005])
    assert abs(10 * result['branch_current_a']['V1'][0] + 2 * .005 ** 2 * 1000) < 1e-12
    assert result['numerics']['max_backward_residual'] < 1e-12


def test_rc_ac_corner_and_phase():
    result = analyze_circuit({'mode': 'ac', 'frequencies': [1 / (2 * math.pi * .001)],
        'elements': [element('V1', 'V', 'in', '0', 0, ac_magnitude=1),
                     element('R1', 'R', 'in', 'out', 1000), element('C1', 'C', 'out', '0', 1e-6)]})
    assert result['ok'], result
    assert result['node_voltage_v']['out']['magnitude'] == pytest.approx([1 / math.sqrt(2)])
    assert result['node_voltage_v']['out']['phase_deg'] == pytest.approx([-45])


def test_rc_step_response_converges_with_timestep_refinement():
    errors = []
    for dt in (.0001, .00005):
        result = analyze_circuit({'mode': 'transient', 'dt': dt, 'stop': .001,
            'elements': [element('V1', 'V', 'in', '0', 0, step={'initial': 0, 'final': 1}),
                         element('R1', 'R', 'in', 'out', 1000), element('C1', 'C', 'out', '0', 1e-6)]})
        assert result['ok'], result
        errors.append(abs(result['node_voltage_v']['out'][-1] - (1 - math.exp(-1))))
    assert errors[1] < errors[0] and errors[1] < .01


def test_invalid_and_unsupported_models_never_become_successes():
    for elements in ([element('R1', 'R', 'a', 'b', 100)],
                     [element('D1', 'diode', 'a', '0', .7)],
                     [element('R1', 'R', 'a', '0', -1)],
                     [element('V1', 'V', 'a', '0', 1), element('V2', 'V', 'a', '0', 2)]):
        result = analyze_circuit({'elements': elements})
        assert not result['ok'] and result['status'] == 'blocked'
        assert 'node_voltage_v' not in result


def test_project_api_retains_request_and_result_evidence(tmp_path):
    from r3_orchestrator.api import analyze_circuit as run
    import json
    (tmp_path / 'robot.yaml').write_text('schema: r3/robot@1\nrobot:\n  name: circuit-test\ncomponents: {}\n')
    result = run(tmp_path, {'elements': [element('V1', 'V', 'a', '0', 1), element('R1', 'R', 'a', '0', 1000)]})
    assert result['ok']
    saved = json.loads((tmp_path / 'build' / result['artifact']).read_text())
    assert saved['request']['elements'][0]['id'] == 'V1'
    assert saved['result']['request_sha256'] == result['request_sha256']


def test_extreme_timestep_is_blocked_before_allocating_or_rounding():
    result = analyze_circuit({'mode': 'transient', 'dt': 1e-320, 'stop': 1,
        'elements': [element('V1', 'V', 'a', '0', 1), element('R1', 'R', 'a', '0', 1000)]})
    assert not result['ok'] and result['code'] == 'circuit_model_invalid'
    assert 'admission limit' in result['message']
