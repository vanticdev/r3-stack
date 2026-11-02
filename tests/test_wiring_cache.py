"""Numeric work is reusable; native identity and engineering checks stay authoritative."""
import zipfile

import numpy as np
import pytest

from r3_wiring.cache import NumericCache, shape_identity
from r3_wiring.geometry import Box, MeshShape, SweptShape
from r3_wiring.grid import VoxelGrid, compute_fields
from r3_wiring.obstacles import Obstacle, ObstacleSet
from r3_wiring.router import HarnessRouter, RouterOptions, _Context


def fields_fixture():
    shape = Box(np.zeros(3), np.eye(3), np.array([.004, .006, .008]))
    grid = VoxelGrid(np.array([-.02, -.02, -.02]), (9, 9, 9), .005)
    return grid, ObstacleSet([Obstacle('fixture', 'structure', shape, shape, .001, False, 'base', True)], None)


def test_fields_survive_process_cache_recreation_with_bitwise_identical_values(tmp_path, monkeypatch):
    grid, obstacles = fields_fixture()
    cache = NumericCache(tmp_path/'cache')
    cold = compute_fields(grid, obstacles, {'fixture'}, cache=cache)
    def forbidden(*args):
        raise AssertionError('An unchanged shape must not recompute its SDF')
    monkeypatch.setattr(Box, 'sdf_bounded', forbidden)
    warm_cache = NumericCache(tmp_path/'cache')
    warm = compute_fields(grid, obstacles, {'fixture'}, cache=warm_cache)
    for name in ('static_clear','static_raw','moving_keep','moving_raw','struct_dist'):
        np.testing.assert_array_equal(getattr(cold, name), getattr(warm, name))
    np.testing.assert_array_equal(cold.by_component['fixture'], warm.by_component['fixture'])
    assert warm_cache.stats['field_hits'] == 1


@pytest.mark.parametrize('change', ['geometry', 'pose', 'grid', 'epoch'])
def test_relevant_inputs_invalidate_only_the_changed_field(tmp_path, monkeypatch, change):
    grid, obstacles = fields_fixture();cache=NumericCache(tmp_path/'cache')
    compute_fields(grid, obstacles, cache=cache)
    if change == 'geometry': obstacles.obstacles[0].shape.half[0] += .001
    elif change == 'pose': obstacles.obstacles[0].shape.center[0] += .001
    elif change == 'grid': grid.voxel += .001
    else: monkeypatch.setattr('r3_wiring.cache.kernel_identity',lambda:'different-code')
    compute_fields(grid, obstacles, cache=cache)
    assert cache.stats['field_misses'] == 2


def test_keepouts_and_classification_recompute_on_a_raw_field_hit(tmp_path):
    grid, obstacles = fields_fixture();cache=NumericCache(tmp_path/'cache')
    first=compute_fields(grid,obstacles,cache=cache)
    obstacles.obstacles[0].keepout += .002
    second=compute_fields(grid,obstacles,cache=cache)
    assert cache.stats['field_hits']==1
    np.testing.assert_array_equal(first.static_raw,second.static_raw)
    np.testing.assert_allclose(second.static_clear,first.static_clear-.002,atol=1e-15)


def search_router(cache):
    router=HarnessRouter.__new__(HarnessRouter)
    router.grid=VoxelGrid(np.zeros(3),(5,5,5),.003)
    router.opt=RouterOptions();router.cache=cache;router.expansions=0;router._build_moves()
    n=router.grid.size;flat=np.arange(n)
    router._ci=(flat//25).tolist();router._cj=((flat//5)%5).tolist();router._ck=(flat%5).tolist()
    router.hole_mask=bytearray(n)
    cost=np.ones(n);zero=np.zeros(n);mask=bytearray(n)
    ctx=_Context(.001,mask,np.asarray(mask),mask,np.asarray(mask),zero.tolist(),cost.tolist(),cost,zero)
    return router,ctx,bytearray(router.grid.border()),bytearray(n)


def test_search_hit_skips_search_but_changed_occupancy_recomputes(tmp_path, monkeypatch):
    router,ctx,blocked,used=search_router(NumericCache(tmp_path/'cache'))
    start,goal=31,93
    cold=router._astar(start,goal,ctx,blocked,used,None,None)
    assert cold and router.expansions>0
    def forbidden(*args): raise AssertionError('Search should be cached')
    monkeypatch.setattr(router,'_astar_uncached',forbidden)
    assert router._astar(start,goal,ctx,blocked,used,None,None)==cold
    blocked[cold[len(cold)//2]]=1
    with pytest.raises(AssertionError,match='cached'):
        router._astar(start,goal,ctx,blocked,used,None,None)


def test_corruption_and_symlinks_never_supply_a_field_or_evict_unrelated_files(tmp_path):
    cache=NumericCache(tmp_path/'cache',max_entries=1);key=cache.key('field','fixture')
    cache.put(key,np.arange(5,dtype=float));path=cache.root/(key+'.npz')
    path.write_bytes(b'invalid archive')
    assert cache.get('field',key,size=5,dtype=float) is None
    sentinel=tmp_path/'unrelated';sentinel.mkdir();(sentinel/('a'*64+'.npz')).write_bytes(b'keep')
    link=tmp_path/'linked';link.symlink_to(sentinel,target_is_directory=True)
    assert not NumericCache(link,max_bytes=0).enabled
    assert (sentinel/('a'*64+'.npz')).read_bytes()==b'keep'
    assert not NumericCache(sentinel,max_bytes=0).enabled


def test_eviction_is_bounded_and_only_removes_owned_entries(tmp_path):
    cache=NumericCache(tmp_path/'cache',max_entries=1)
    unrelated=cache.root/'notes.txt';unrelated.write_text('keep')
    for value in ('first','second'):cache.put(cache.key('field',value),np.arange(5,dtype=float))
    assert len(list(cache.root.glob('*.npz')))==1
    assert unrelated.read_text()=='keep' and cache.stats['evictions']==1


def test_mesh_samples_and_swept_poses_are_in_the_identity():
    import trimesh
    mesh=trimesh.creation.box([.01,.02,.03]);shape=MeshShape(mesh.vertices,mesh.faces)
    first=shape_identity(shape)
    shape._local[0][0,0]+=.0001
    assert shape_identity(shape)!=first
    a=SweptShape([shape]);b=SweptShape([shape.transformed(np.eye(3),np.array([.001,0,0]))])
    assert shape_identity(a)!=shape_identity(b)


@pytest.mark.parametrize('change',['cost','used','zone','hole','direction','limit'])
def test_search_keys_cover_every_search_constraint(tmp_path,monkeypatch,change):
    router,ctx,blocked,used=search_router(NumericCache(tmp_path/'cache'))
    router._astar(31,93,ctx,blocked,used,None,None)
    direction=None
    if change=='cost':ctx.cost[62]+=.1
    elif change=='used':used[62]=1
    elif change=='zone':ctx.zone[62]=1
    elif change=='hole':router.hole_mask[62]=1
    elif change=='direction':direction=np.array([1.,0.,0.])
    else:router.opt.max_expansions-=1
    def recompute(*args):raise RuntimeError('Changed search must recompute')
    monkeypatch.setattr(router,'_astar_uncached',recompute)
    with pytest.raises(RuntimeError,match='recompute'):
        router._astar(31,93,ctx,blocked,used,direction,None)


def test_one_changed_obstacle_keeps_the_other_cached_field(tmp_path):
    grid,obstacles=fields_fixture()
    a=Box(np.array([.01,0.,0.]),np.eye(3),np.array([.002,.003,.004]))
    obstacles.obstacles.append(Obstacle('second','structure',a,a,0.,False,'base',False))
    cache=NumericCache(tmp_path/'cache');compute_fields(grid,obstacles,cache=cache)
    obstacles.obstacles[0].shape.center[0]+=.001
    compute_fields(grid,obstacles,cache=cache)
    assert cache.stats['field_misses']==3 and cache.stats['field_hits']==1


def test_real_native_fixture_routes_match_and_warm_search_expands_zero_nodes(tmp_path):
    from r3_model.project import Project
    from r3_wiring.obstacles import build_obstacles
    from test_wiring_support import write_project
    project=Project.load(write_project(tmp_path,block=True))
    obstacles,_=build_obstacles(project)
    cold_router=HarnessRouter(project,obstacles,RouterOptions(plan=False))
    cold=cold_router.route()
    assert cold.wires and not any(wire.failed for wire in cold.wires.values())
    warm_router=HarnessRouter(project,obstacles,RouterOptions(plan=False))
    warm=warm_router.route()
    assert cold_router.expansions>0 and warm_router.expansions==0
    assert warm.stats['cache']['field_hits']>=len(obstacles.obstacles)
    assert warm.stats['cache']['search_hits']>0
    assert cold.wires.keys()==warm.wires.keys()
    for name in cold.wires:
        np.testing.assert_array_equal(cold.wires[name].path,warm.wires[name].path)
        np.testing.assert_array_equal(cold.wires[name].bundle_radius,warm.wires[name].bundle_radius)
        assert cold.wires[name].failed==warm.wires[name].failed
    assert [(d.code,d.severity) for d in cold.diagnostics]==[(d.code,d.severity) for d in warm.diagnostics]


def test_valid_archive_with_changed_values_is_rejected_by_checksum(tmp_path):
    cache=NumericCache(tmp_path/'cache');key=cache.key('field','fixture');cache.put(key,np.arange(5,dtype=float))
    path=cache.root/(key+'.npz')
    with np.load(path,allow_pickle=False) as data: checksum=str(data['checksum'].item())
    np.savez_compressed(path,value=np.arange(5,dtype=float)+1,checksum=checksum,key=key)
    assert cache.get('field',key,size=5,dtype=float) is None
    assert cache.stats['corrupt']==1


def test_forged_huge_array_header_is_rejected_before_loading(tmp_path):
    from io import BytesIO
    cache=NumericCache(tmp_path/'cache');key=cache.key('field','fixture')
    header=BytesIO();np.lib.format.write_array_header_1_0(header,{'descr':'<f8','fortran_order':False,'shape':(10**12,)})
    with zipfile.ZipFile(cache.root/(key+'.npz'),'w') as archive:
        archive.writestr('value.npy',header.getvalue())
        for name,value in [('key',key),('checksum','0'*64)]:
            file=BytesIO();np.save(file,np.array(value),allow_pickle=False);archive.writestr(name+'.npy',file.getvalue())
    assert cache.get('field',key,size=5,dtype=float) is None


def test_failed_search_reuses_only_the_same_blocked_problem(tmp_path,monkeypatch):
    router,ctx,blocked,used=search_router(NumericCache(tmp_path/'cache'))
    blocked[:]=bytes([1])*len(blocked);blocked[31]=0;blocked[93]=0
    assert router._astar(31,93,ctx,blocked,used,None,None) is None
    def forbidden(*args):raise AssertionError('Identical failed search must be cached')
    monkeypatch.setattr(router,'_astar_uncached',forbidden)
    assert router._astar(31,93,ctx,blocked,used,None,None) is None
    blocked[62]=0
    with pytest.raises(AssertionError,match='cached'):
        router._astar(31,93,ctx,blocked,used,None,None)
