"""Evidence-preserving debugging groups shared by HTTP, reports and agents.

Only an explicit cause_id establishes a causal group. Subject relationships
are labeled context, never promoted to a confirmed root cause by similar text.
This is a view of findings, not a durable issue-resolution ledger.
"""
from __future__ import annotations

import hashlib
import json


def debug_work_units(diagnostics: list[dict]) -> list[dict]:
    groups = {}
    rank = {'error': 0, 'warning': 1, 'info': 2}
    for finding in diagnostics:
        data = finding.get('data') or {}
        stage = finding.get('stage') or 'general'
        cause = data.get('cause_id')
        subjects = sorted(set(finding.get('subjects') or []))
        keys = [f'cause:{cause}'] if cause else [f'context:{stage}:{s}' for s in subjects] or [f'context:{stage}:general']
        canonical = json.dumps(finding, sort_keys=True, default=str)
        evidence_id = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        for key in keys:
            group = groups.setdefault(key, {'id': hashlib.sha256(key.encode()).hexdigest()[:16],
                'title': f'Investigate {cause or key.split(":", 2)[-1]}',
                'relationship': 'explicit_cause' if cause else 'related_context',
                'cause_status': data.get('cause_status', 'suspected') if cause else 'not_established',
                'severity': 'info', 'stages': set(), 'subjects': set(), 'evidence': {},
                'recommended_actions': set(), 'status': 'unresolved'})
            if cause and group['cause_status'] != data.get('cause_status', 'suspected'):
                group['cause_status'] = 'suspected'
            if rank.get(finding.get('severity'), 2) < rank[group['severity']]:
                group['severity'] = finding['severity']
            group['stages'].add(stage)
            group['subjects'].update(subjects)
            entry = group['evidence'].setdefault(evidence_id, {'id': evidence_id, 'finding': finding, 'occurrences': 0})
            entry['occurrences'] += 1
            if finding.get('hint'):
                group['recommended_actions'].add(finding['hint'])
    result = []
    for group in groups.values():
        group['stages'] = sorted(group['stages'])
        group['subjects'] = sorted(group['subjects'])
        group['evidence'] = list(group['evidence'].values())
        group['recommended_actions'] = sorted(group['recommended_actions'])
        group['acceptance_criteria'] = [
            'Reproduce the reported condition on the recorded inputs.',
            f'Rerun relevant checks for {", ".join(group["stages"])} on the changed revision.',
            'Verify the original failures are absent without removing checks or weakening constraints.',
            'Record tests and remaining issues; agent completion alone does not prove resolution.']
        result.append(group)
    return sorted(result, key=lambda g: (rank[g['severity']], g['id']))
