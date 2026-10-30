"""Full-stage profiler and immutable comparison snapshots; run from the repo root."""
import cProfile
import hashlib
import json
import os
import pstats
import shutil
import signal
import time
import traceback
from pathlib import Path
from r3_model.project import Project
from r3_orchestrator.stages import run_stage

label = os.environ.get('R3_SPEED_LABEL', 'before')
out = Path('.r3-runs/wiring-speed') / label
out.mkdir(parents=True, exist_ok=True)
project = Project.load(os.environ.get('R3_SPEED_PROJECT', 'examples/robots/rc-car'))
# Wiring reads CAD, connector display geometry, the spec/lock and project catalog.
# Snapshot their bytes, independent of path and generated wiring outputs.
inputs = {}
for path in sorted(project.root.rglob('*')):
    if not path.is_file() or '__pycache__' in path.parts or '.git' in path.parts:
        continue
    relative = path.relative_to(project.root)
    if relative.parts[:2] == ('build', 'wiring'):
        continue
    if relative.suffix in ('.yaml', '.json', '.stl', '.glb', '.step', '.stp'):
        inputs[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
(out / 'input-hashes.json').write_text(json.dumps(inputs, indent=2))
profiler = cProfile.Profile()

def snapshot(signum, frame):
    # Profile.dump_stats/create_stats disable profiling, so checkpoints contain
    # only a stack trace. The uninterrupted full profile is dumped on completion.
    (out / 'progress.txt').write_text(f'Elapsed: {time.perf_counter() - started:.3f} s\n' + ''.join(traceback.format_stack(frame)))

signal.signal(signal.SIGUSR1, snapshot)
signal.signal(signal.SIGALRM, snapshot)
signal.setitimer(signal.ITIMER_REAL, 60, 60)
started = time.perf_counter()
result = profiler.runcall(run_stage, project, 'wiring')
wall = time.perf_counter() - started
signal.setitimer(signal.ITIMER_REAL, 0)
profiler.dump_stats(str(out / 'profile.pstats'))
with (out / 'profile.txt').open('w') as stream:
    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(100)
for name in ('routes.json', 'result.json', 'routing_detail.json'):
    shutil.copyfile(project.root / 'build/wiring' / name, out / name)
timing = {'wall_s': wall, 'stage_s': result.duration_s, 'ok': result.ok,
          'diagnostic_count': len(result.diagnostics), 'source_revision': os.environ.get('R3_SPEED_SOURCE', 'working-tree')}
(out / 'timing.json').write_text(json.dumps(timing, indent=2))
print(json.dumps(timing, indent=2), flush=True)
