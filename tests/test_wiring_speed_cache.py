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


def test_plan_cache_reuses_only_solve_and_invalidates_every_numeric_input(tmp_path, monkeypatch):
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
    planner.plan_bundle([segment], junctions, fields, .008, cache_dir=tmp_path)
    assert calls
    calls.clear()
    planner.plan_bundle([segment], junctions, fields, .009, cache_dir=tmp_path)
    assert calls
