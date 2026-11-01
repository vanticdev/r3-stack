"""Pinned native mass properties, including explicit Onshape overrides.

The API returns calculated, lower and upper blocks (not per-entry triples).
Inertia is about the center of mass, expressed in the source axes:
https://cad.onshape.com/help/Content/View/mass_properties_tool.htm
"""
from __future__ import annotations

from typing import Any

import numpy as np

from r3_model.pulled import PullError


def properties(body: Any) -> dict[str, Any]:
    def invalid(message: str) -> PullError:
        return PullError('cad_pull_mass_unavailable', message)
    if not isinstance(body, dict) or body.get('hasMass') is not True or body.get('massMissingCount') != 0:
        raise invalid('Native mass is missing or incomplete; assign native properties to every constituent body')

    def values(name: str, count: int) -> np.ndarray:
        try:
            data = np.asarray(body.get(name), dtype=float)
        except (TypeError, ValueError):
            raise invalid(f'Native {name} must contain finite calculated values') from None
        if data.shape not in ((count,), (count * 3,)) or not np.isfinite(data).all():
            raise invalid(f'Native {name} has an unsupported or non-finite layout')
        calculated = data[:count]
        if len(data) == count * 3:
            if np.any(data[count:count * 2] > calculated) or np.any(data[count * 2:] < calculated):
                raise invalid(f'Native {name} calculation bounds are inconsistent')
        return calculated

    mass, volume = float(values('mass', 1)[0]), float(values('volume', 1)[0])
    center, inertia = values('centroid', 3), values('inertia', 9).reshape(3, 3)
    if mass <= 0 or volume <= 0:
        raise invalid('Native mass and volume must be positive')
    if not np.allclose(inertia, inertia.T, rtol=1e-10, atol=0):
        raise invalid('Native center-of-mass inertia must be symmetric')
    moments = np.linalg.eigvalsh(inertia)
    if moments[0] <= 0 or moments[0] + moments[1] < moments[2]:
        raise invalid('Native inertia must be positive and satisfy the rigid-body triangle inequality')
    return {'mass': mass, 'volume': volume, 'com': center.tolist(), 'inertia': inertia.reshape(-1).tolist(),
            'mass_properties_source': 'onshape-native-overrides-enabled'}


def selected_body(response: Any, part_id: str) -> dict[str, Any]:
    bodies = response.get('bodies') if isinstance(response, dict) else None
    if not isinstance(bodies, dict) or len(bodies) != 1 or next(iter(bodies)) not in (part_id, '-all-'):
        raise PullError('cad_pull_mass_unavailable', 'Native mass response must identify the exact selected part')
    body = next(iter(bodies.values()))
    properties(body)  # Fail before geometry downloads, never substitute a density estimate.
    return body
