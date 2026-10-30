"""Compare complete wiring snapshots without ignoring diagnostics or route validity."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

# Stricter than the millimetre-scale physical checks: one exported nanometre
# plus NumPy assert_allclose's default relative tolerance.
RTOL = 1e-7
ATOL = 1e-9


def compare(before, after, path, differences):
    if isinstance(before, bool) or isinstance(after, bool):
        equal = type(before) is type(after) and before == after
    elif isinstance(before, (int, float)) and isinstance(after, (int, float)):
        equal = before == after or math.isclose(before, after, rel_tol=RTOL, abs_tol=ATOL)
    elif isinstance(before, dict) and isinstance(after, dict):
        if before.keys() != after.keys():
            differences.append({'path': path, 'before_keys': sorted(before), 'after_keys': sorted(after)})
        for key in before.keys() & after.keys():
            compare(before[key], after[key], f'{path}.{key}', differences)
        return
    elif isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            differences.append({'path': path, 'before_length': len(before), 'after_length': len(after)})
        for i, (a, b) in enumerate(zip(before, after)):
            compare(a, b, f'{path}[{i}]', differences)
        return
    else:
        equal = before == after
    if not equal:
        differences.append({'path': path, 'before': repr(before), 'after': repr(after)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', type=Path, default=Path('.r3-runs/wiring-speed-proof.json'))
    parser.add_argument('--tests', type=Path)
    parser.add_argument('--before-cpu', type=Path)
    parser.add_argument('--after-cpu', type=Path)
    parser.add_argument('--before-tests-cpu', type=Path)
    parser.add_argument('--after-tests-cpu', type=Path)
    args = parser.parse_args()
    load = lambda folder, name: json.loads((folder / name).read_text())
    old, new = load(args.before, 'routes.json'), load(args.after, 'routes.json')
    old_result, new_result = load(args.before, 'result.json'), load(args.after, 'result.json')
    mismatches = []
    completed = all(not any(d['code'] == 'stage_crashed' for d in r['diagnostics'])
                    and 'wires' in r.get('metrics', {}) and 'release' in r.get('metrics', {})
                    for r in (old_result, new_result))
    if not completed:
        mismatches.append({'path': 'stage_completion', 'reason': 'stage crashed or final metrics are missing'})
    compare(old, new, 'routes', mismatches)
    # Wall time, start time and the implementation's Git revision are expected
    # to differ. Every other result field, including complete diagnostics, is checked.
    for result in (old_result, new_result):
        for key in ('duration_s', 'started_at'):
            result.pop(key, None)
        result.get('inputs', {}).pop('git_commit', None)
    compare(old_result, new_result, 'result', mismatches)
    old_inputs, new_inputs = load(args.before, 'input-hashes.json'), load(args.after, 'input-hashes.json')
    compare(old_inputs, new_inputs, 'input_content_hashes', mismatches)
    path_count = 0
    max_difference = 0.
    shapes_equal = True
    for section, key in (('wires', 'path'), ('harnesses', 'bundle_path')):
        for identity in old[section].keys() & new[section].keys():
            a, b = np.asarray(old[section][identity][key]), np.asarray(new[section][identity][key])
            path_count += 1
            if a.shape != b.shape:
                shapes_equal = False
            elif a.size:
                max_difference = max(max_difference, float(np.abs(a - b).max()))
    counts = lambda result: dict(sorted(Counter(f"{d['severity']}:{d['code']}" for d in result['diagnostics']).items()))
    proof = {
        'equal': not mismatches,
        'stage_runs_completed': completed,
        'tolerance': {'rtol': RTOL, 'atol_m': ATOL},
        'input_content_equal': old_inputs == new_inputs,
        'input_files': len(old_inputs),
        'paths_compared': path_count,
        'path_shapes_equal': shapes_equal,
        'max_absolute_path_difference_m': max_difference,
        'routes_byte_identical': hashlib.sha256((args.before/'routes.json').read_bytes()).digest() == hashlib.sha256((args.after/'routes.json').read_bytes()).digest(),
        'diagnostics_before': counts(old_result),
        'diagnostics_after': counts(new_result),
        'diagnostic_count_before': len(old_result['diagnostics']),
        'diagnostic_count_after': len(new_result['diagnostics']),
        'release_before': old['release'],
        'release_after': new['release'],
        'before_timing': load(args.before, 'timing.json'),
        'after_timing': load(args.after, 'timing.json'),
        'mismatch_count': len(mismatches),
        'first_mismatches': mismatches[:40],
    }
    if args.tests:
        output = args.tests.read_text()
        summary = re.search(r'(\d+) passed(?:, \d+ (?:skipped|warnings?))* in ([\d.]+)s', output)
        proof['test_summary'] = summary.group(0) if summary else output[-1500:]
    for label, path in (('before_cpu', args.before_cpu), ('after_cpu', args.after_cpu),
                        ('before_tests', args.before_tests_cpu), ('after_tests', args.after_tests_cpu)):
        if path:
            data = json.loads(path.read_text())
            samples = data.get('samples', data.get('load_samples', []))
            loads = [s['load'][0] if 'load' in s else s['load_1m'] for s in samples]
            proof[label] = {k: data[k] for k in ('wall_s', 'user_s', 'system_s', 'cpu_s',
                           'cpu_method', 'method', 'observed_peak_processes', 'logical_cpus',
                           'exit_code', 'sampling_interval_s', 'cpu_total_covers_full_process_lifetime') if k in data}
            if loads:
                proof[label]['load_1m'] = {'min': min(loads), 'max': max(loads), 'mean': sum(loads)/len(loads)}
            machine_counts = [s['machine_processes'] for s in samples if 'machine_processes' in s]
            if machine_counts:
                proof[label]['machine_processes'] = {
                    'min': min(machine_counts), 'max': max(machine_counts),
                    'mean': sum(machine_counts) / len(machine_counts),
                }
            elif 'observed_peak_machine_processes' in data:
                proof[label]['machine_processes'] = {
                    'min': data['observed_min_machine_processes'],
                    'max': data['observed_peak_machine_processes'],
                }
            if 'load_observation_started_at' in data:
                proof[label]['load_observation_started_at'] = data['load_observation_started_at']
            proof[label]['load_observations'] = len(samples)
    proof['validation_passed'] = proof['equal'] and all(
        proof[label].get('exit_code') == 0 for label in ('before_tests', 'after_tests') if label in proof)
    args.output.write_text(json.dumps(proof, indent=2) + '\n')
    print(json.dumps({k: proof[k] for k in ('equal', 'input_content_equal', 'paths_compared', 'max_absolute_path_difference_m', 'mismatch_count')}, indent=2))
    raise SystemExit(0 if proof['validation_passed'] else 1)


if __name__ == '__main__':
    main()
