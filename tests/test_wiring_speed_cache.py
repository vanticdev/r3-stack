"""Content identity and safe, deterministic reuse of numeric wiring computations."""
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from r3_wiring.cache import NumericCache, content_key


def test_content_keys_preserve_numeric_precision_shape_type_and_mapping_order():
    a = np.array([[0., 1., 2.]])
    assert content_key(a) == content_key(a.copy())
    assert content_key(a) != content_key(a.astype(np.float32))
    assert content_key(a) != content_key(a.ravel())
    b = a.copy()
    b[0, 0] = np.nextafter(0., 1.)
    assert content_key(a) != content_key(b)
    assert content_key({'a': 1, 'b': 2}) != content_key({'b': 2, 'a': 1})
    assert content_key({'a', 'b'}) == content_key({'b', 'a'})


def test_numeric_cache_round_trip_corruption_disable_and_concurrent_writers(tmp_path, monkeypatch):
    cache = NumericCache(tmp_path, 'numeric')
    key = cache.key(np.array([1., 2.]), {'limit': 1e-10})
    arrays, value = {'x': np.array([1., np.pi])}, {'iterations': 300, 'message': 'complete'}
    assert cache.load(key) is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: cache.store(key, arrays, value), range(8)))
    actual, info = cache.load(key)
    np.testing.assert_array_equal(actual['x'], arrays['x'])
    assert info == value
    actual['x'][0] = -1
    np.testing.assert_array_equal(cache.load(key)[0]['x'], arrays['x'])
    (tmp_path / 'numeric' / f'{key}.npz').write_bytes(b'broken')
    assert cache.load(key) is None
    monkeypatch.setenv('R3_WIRING_CACHE', '0')
    disabled = NumericCache(tmp_path, 'disabled')
    disabled.store(key, arrays, value)
    assert disabled.load(key) is None
    assert not (tmp_path / 'disabled').exists()


def test_empty_arrays_are_cacheable():
    assert content_key(np.zeros((0, 3))) == content_key(np.empty((0, 3)))


def test_plan_cache_reuses_only_solve_and_validates_every_voxel_read(tmp_path, monkeypatch):
    import r3_wiring.planner as planner
    from test_wiring_feasible_stop import _fixed_exit_problem

    segment, junctions, fields = _fixed_exit_problem()
    minimize = planner.minimize
    calls = []

    def recorded(*args, **kwargs):
        calls.append(kwargs['method'])
        return minimize(*args, **kwargs)

    monkeypatch.setattr(planner, 'minimize', recorded)
    original = planner.plan_bundle([segment], junctions, fields, .008, cache_dir=tmp_path)
    assert calls and original.violations
    calls.clear()
    # Cached states must pass the entire final audit again, including witnesses.
    audit = planner._violations
    audits = []

    def audited(*args, **kwargs):
        audits.append(True)
        return audit(*args, **kwargs)

    monkeypatch.setattr(planner, '_violations', audited)
    cached = planner.plan_bundle([segment], junctions, fields, .008, cache_dir=tmp_path)
    assert not calls and audits
    assert original.stats == cached.stats
    assert [v.as_dict() for v in original.violations] == [v.as_dict() for v in cached.violations]
    for a, b in zip(original.segments, cached.segments):
        np.testing.assert_array_equal(a.points, b.points)
    fields.hardware = fields.hardware.copy()
    fields.hardware[0] = np.nextafter(fields.hardware[0], np.inf)
    remote = planner.plan_bundle([segment], junctions, fields, .008, cache_dir=tmp_path)
    assert not calls  # this voxel was never read anywhere along the original solve
    exact = planner.plan_bundle([segment], junctions, fields, .008)
    assert calls and exact.stats == remote.stats
    np.testing.assert_array_equal(exact.segments[0].points, remote.segments[0].points)
    calls.clear()
    from r3_wiring.cache import NumericCache

    file = next((tmp_path / 'plans-v2').rglob('*.npz'))
    arrays, _ = NumericCache(file.parent.parent, file.parent.name).load(file.stem)
    touched = arrays['hardware_indices'][0]
    fields.hardware[touched] = np.nextafter(fields.hardware[touched], np.inf)
    planner.plan_bundle([segment], junctions, fields, .008, cache_dir=tmp_path)
    assert calls
    calls.clear()
    planner.plan_bundle([segment], junctions, fields, .009, cache_dir=tmp_path)
    assert calls


def test_field_read_footprint_covers_all_gradient_corners_and_signed_zero():
    from r3_wiring.cache import field_probes, fields_match
    from r3_wiring.grid import VoxelGrid
    from r3_wiring.planner import ClearanceField

    grid = VoxelGrid(np.zeros(3), (5, 5, 5), .001)
    fields = ClearanceField(grid, np.zeros(grid.size), np.zeros(grid.size), np.zeros(grid.size))
    fields._visited = {name: np.zeros(grid.size, bool) for name in ('static', 'moving', 'structure')}
    # At a grid corner, several interpolation weights are zero but their
    # gradients can still depend on adjacent values. Record every corner.
    fields.sample('static', np.array([[.001, .001, .001]]))
    arrays = field_probes(fields)
    expected = np.array([(x * 5 + y) * 5 + z for x in (1, 2) for y in (1, 2) for z in (1, 2)])
    np.testing.assert_array_equal(arrays['static_indices'], expected)
    assert fields_match(fields, arrays)
    fields.static[0] = 9.
    assert fields_match(fields, arrays)
    fields.static[expected[-1]] = -0.
    assert not fields_match(fields, arrays)
    fields.static[expected[-1]] = 1e-10
    assert not fields_match(fields, arrays)


def _process_solution(directory):
    from pathlib import Path
    from r3_wiring.planner import plan_bundle
    from test_wiring_feasible_stop import _fixed_exit_problem

    segment, junctions, fields = _fixed_exit_problem()
    plan = plan_bundle([segment], junctions, fields, .008, cache_dir=Path(directory))
    return {'points': [s.points.tolist() for s in plan.segments],
            'violations': [v.as_dict() for v in plan.violations], 'stats': plan.stats}


def test_serial_and_multiple_processes_produce_identical_solutions(tmp_path):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    serial = _process_solution(str(tmp_path / 'serial'))
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn')) as pool:
        results = list(pool.map(_process_solution, [str(tmp_path / 'parallel')] * 4))
    assert results == [serial] * 4
