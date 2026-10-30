"""Report nested phase costs from a complete cProfile run, without machine paths."""
import argparse
import json
from pathlib import Path
import pstats

parser=argparse.ArgumentParser()
parser.add_argument('profile',type=Path)
args=parser.parse_args()
stats=pstats.Stats(str(args.profile))

def cumulative(file,name):
    return sum(v[3] for k,v in stats.stats.items() if k[0].replace('\\','/').endswith('/r3_wiring/'+file) and k[2]==name)

planner_from_router=0.
for k,v in stats.stats.items():
    if k[0].endswith('/r3_wiring/planner.py') and k[2]=='plan_routing':
        planner_from_router=sum(value[-1] for caller,value in v[4].items()
                                if caller[0].endswith('/r3_wiring/router.py') and caller[2]=='route')
renderers=['render_routes','render_slack','render_supports','render_support_details',
           'render_witnesses','render_planning','render_derating']
timing_path=args.profile.parent/'timing.json'
render_time=sum(v[3] for k,v in stats.stats.items() if '/r3_wiring/' in k[0] and k[2] in renderers)
render_note='sum of renderer cumulative times'
if not render_time and timing_path.exists():
    timing=json.loads(timing_path.read_text())
    # Some thread-heavy cProfile runs lose the still-active renderer/parent
    # frames. Independent stage wall time bounds this small output remainder.
    render_time=max(0.,timing['stage_s']-cumulative('stage.py','route_and_check')
                    -cumulative('driver.py','run_drive')-cumulative('manufacturing.py','write_package'))
    render_note='wall-time remainder after route/check, drive, manufacturing; includes JSON/output overhead'
report={
    'nested_cumulative_seconds':{
        'voxel_fields':cumulative('grid.py','compute_fields'),
        'voxel_routing_including_fields_excluding_planner':cumulative('grid.py','compute_fields')+cumulative('router.py','route')-planner_from_router,
        'constrained_planning':cumulative('planner.py','plan_routing'),
        'native_slsqp':sum(v[2] for k,v in stats.stats.items() if '._slsqplib.slsqp' in k[2]),
        'collision_queries':cumulative('geometry.py','_query')+cumulative('witness.py','_mesh_closest'),
        'support_placement':cumulative('supports.py','place_supports'),
        'slack_simulation':cumulative('driver.py','run_drive'),
        'png_rendering_and_output':render_time,
        'manufacturing_including_wireviz':cumulative('manufacturing.py','write_package'),
    },
    'function_calls':stats.total_calls,
    'primitive_calls':stats.prim_calls,
    'render_measurement':render_note,
    'note':'Phase costs are nested; collision queries and native SLSQP are included in their calling phases.',
}
print(json.dumps(report,indent=2))
