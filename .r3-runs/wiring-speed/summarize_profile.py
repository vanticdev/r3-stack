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
report={
    'nested_cumulative_seconds':{
        'voxel_fields':cumulative('grid.py','compute_fields'),
        'voxel_routing_including_fields_excluding_planner':cumulative('grid.py','compute_fields')+cumulative('router.py','route')-planner_from_router,
        'constrained_planning':cumulative('planner.py','plan_routing'),
        'native_slsqp':sum(v[2] for k,v in stats.stats.items() if '._slsqplib.slsqp' in k[2]),
        'collision_queries':cumulative('geometry.py','_query')+cumulative('witness.py','_mesh_closest'),
        'support_placement':cumulative('supports.py','place_supports'),
        'slack_simulation':cumulative('driver.py','run_drive'),
        'png_rendering':sum(v[3] for k,v in stats.stats.items()
                            if '/r3_wiring/' in k[0] and k[2] in renderers),
        'manufacturing_including_wireviz':cumulative('manufacturing.py','write_package'),
    },
    'function_calls':stats.total_calls,
    'primitive_calls':stats.prim_calls,
    'profiled_main_thread_seconds':stats.total_tt,
    'note':'Phase costs are nested; collision queries and native SLSQP are included in their calling phases.',
}
print(json.dumps(report,indent=2))
