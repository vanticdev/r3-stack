"""Repeated projections reuse work, never editable native or derived geometry."""
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from r3_model import pulled_view
from r3_model.pulled import PullError
from test_cad_pulled_consumers import project, pull


def count_mesh_reads(monkeypatch):
    calls = []
    original = pulled_view.world_mesh

    def read(*args):
        calls.append(args[1])
        return original(*args)

    monkeypatch.setattr(pulled_view, 'world_mesh', read)
    return calls


def test_concurrent_views_share_geometry_conversion_but_revalidate_inputs(project, monkeypatch):
    pull(project)
    calls = count_mesh_reads(monkeypatch)
    original = pulled_view.load_pulled
    validations = []

    def validate(*args):
        validations.append(True)
        return original(*args)

    monkeypatch.setattr(pulled_view, 'load_pulled', validate)
    with ThreadPoolExecutor(max_workers=3) as pool:
        views = list(pool.map(lambda _: pulled_view.cad_view(project), range(3)))
    assert len(validations) == 3
    assert calls == ['coupon']
    assert views[0] == views[1] == views[2]
    # A consumer cannot mutate another consumer's cached metadata.
    views[0]['parts']['coupon']['bbox_min'][0] = 100
    assert pulled_view.cad_view(project) == views[1]


@pytest.mark.parametrize('change', ['delete', 'corrupt'])
def test_derived_file_is_restored_from_native_on_cache_hit(project, monkeypatch, change):
    pull(project)
    calls = count_mesh_reads(monkeypatch)
    first = pulled_view.cad_view(project)
    part = first['parts']['coupon']
    path = project.build_dir('cad') / part['mesh']
    if change == 'delete':
        path.unlink()
    else:
        path.write_bytes(b'untrusted local replacement')
    assert pulled_view.cad_view(project) == first
    assert hashlib.sha256(path.read_bytes()).hexdigest() == part['mesh_sha256']
    assert calls == ['coupon', 'coupon']


def test_native_tampering_is_not_hidden_by_a_warm_projection(project):
    native = pull(project)
    pulled_view.cad_view(project)
    source = native.parts['coupon'].stl
    source.chmod(0o644)
    source.write_bytes(source.read_bytes() + b'edited')
    with pytest.raises(PullError) as error:
        pulled_view.cad_view(project)
    assert error.value.code == 'cad_pull_modified'


def test_new_native_pose_invalidates_projection(project, monkeypatch):
    pull(project, offset=(0, 0, 0))
    calls = count_mesh_reads(monkeypatch)
    first = pulled_view.cad_view(project)['parts']['coupon']
    pull(project, offset=(.2, -.3, .1))
    second = pulled_view.cad_view(project)['parts']['coupon']
    assert second['mesh'] != first['mesh']
    assert second['bbox_min'] == pytest.approx([.2, -.3, .1])
    assert calls == ['coupon', 'coupon']
