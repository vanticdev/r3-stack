"""Explicit linear lumped-circuit analysis, separate from PCB copper geometry.

Modified nodal analysis: G*x + C*dx/dt = b. DC uses G; AC uses
G+j*w*C; transients use fixed-step backward Euler with a DC initial state.
No device models are inferred from footprints. Nonlinear/unknown devices are
rejected by the schema. This module contains no SPIKE source code.
"""
from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu


class CircuitSolveError(ValueError):
    pass


class StepSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    initial: FiniteFloat
    final: FiniteFloat
    delay: FiniteFloat = Field(default=0, ge=0)


class Element(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    kind: Literal['R', 'C', 'L', 'V', 'I']
    positive: str = Field(min_length=1)
    negative: str = Field(min_length=1)
    value: FiniteFloat
    ac_magnitude: FiniteFloat = Field(default=0, ge=0)
    ac_phase_deg: FiniteFloat = 0
    step: StepSource | None = None


class CircuitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    elements: list[Element] = Field(min_length=1, max_length=10000)
    mode: Literal['dc', 'ac', 'transient'] = 'dc'
    frequencies: list[FiniteFloat] = Field(default_factory=list, max_length=10000)
    dt: FiniteFloat = Field(default=.00001, gt=0)
    stop: FiniteFloat = Field(default=.001, gt=0)


def analyze_circuit(payload: dict) -> dict:
    """Return structured evidence, never a success on a singular/unsupported model."""
    limitations = ['Linear lumped R/C/L and independent sources only.',
                   'No PCB parasitic extraction, transmission lines, EM, or thermal field solve.',
                   'Transient integration is fixed-step backward Euler; perform step refinement.']
    try:
        request = CircuitRequest.model_validate(payload)
        elements = request.elements
        if len({e.id for e in elements}) != len(elements):
            raise ValueError('Element IDs must be unique.')
        for e in elements:
            if e.positive == e.negative:
                raise ValueError(f'{e.id}: terminals must differ.')
            if e.kind in ('R', 'C', 'L') and (e.value <= 0 or e.step is not None or e.ac_magnitude):
                raise ValueError(f'{e.id}: passive values must be positive; waveforms belong to sources.')
        nodes = sorted({n for e in elements for n in (e.positive, e.negative)} - {'0'})
        if not any('0' in (e.positive, e.negative) for e in elements):
            raise ValueError('Circuit needs an explicit ground node named 0.')
        branches = [e for e in elements if e.kind in ('V', 'L')]
        count = len(nodes) + len(branches)
        if count > 512:
            raise ValueError('Circuit exceeds the 512-unknown admission limit.')
        if request.mode == 'transient':
            steps = request.stop / request.dt
            if not math.isfinite(steps) or steps > 100000:
                raise ValueError('Transient exceeds the 100000-step admission limit.')
        samples = len(request.frequencies) if request.mode == 'ac' else math.ceil(steps) + 1 if request.mode == 'transient' else 1
        if count * samples > 2_000_000:
            raise ValueError('Circuit output exceeds the 2000000-value admission limit.')
        index = {n: i for i, n in enumerate(nodes)}
        branch_index = {e.id: len(nodes) + i for i, e in enumerate(branches)}
        g, c = np.zeros((count, count)), np.zeros((count, count))

        def add(matrix, a, b, value):
            if a is not None and b is not None:
                matrix[a, b] += value

        def pair(matrix, p, n, value):
            add(matrix, p, p, value)
            add(matrix, n, n, value)
            add(matrix, p, n, -value)
            add(matrix, n, p, -value)

        for e in elements:
            p, n = index.get(e.positive), index.get(e.negative)
            if e.kind == 'R':
                pair(g, p, n, 1 / e.value)
            elif e.kind == 'C':
                pair(c, p, n, e.value)
            elif e.kind in ('V', 'L'):
                k = branch_index[e.id]
                for node, sign in ((p, 1), (n, -1)):
                    add(g, node, k, sign)
                    add(g, k, node, sign)
                if e.kind == 'L':
                    c[k, k] = -e.value

        def rhs(time=None, ac=False):
            out = np.zeros(count, dtype=complex if ac else float)
            for e in elements:
                if e.kind not in ('I', 'V'):
                    continue
                value = e.value
                if ac:
                    value = e.ac_magnitude * np.exp(1j * math.radians(e.ac_phase_deg))
                elif e.step is not None:
                    value = e.step.final if time is not None and time >= e.step.delay else e.step.initial
                if e.kind == 'V':
                    out[branch_index[e.id]] = value
                else:
                    for node, sign in ((index.get(e.positive), -1), (index.get(e.negative), 1)):
                        if node is not None:
                            out[node] += sign * value
            return out

        residuals, conditions = [], []
        factorizations = 0

        def factor(matrix):
            nonlocal factorizations
            condition = float(np.linalg.cond(matrix))
            if not math.isfinite(condition) or condition > 1e16:
                raise CircuitSolveError('Circuit matrix is singular or ill-conditioned; check floating nodes and ideal-source loops.')
            conditions.append(condition)
            factorizations += 1
            return splu(csc_matrix(matrix))

        def solve(matrix, lu, vector):
            values = lu.solve(vector)
            denominator = np.linalg.norm(matrix, ord=np.inf) * np.linalg.norm(values, ord=np.inf) + np.linalg.norm(vector, ord=np.inf)
            residual = float(np.linalg.norm(matrix @ values - vector, ord=np.inf) / max(denominator, 1e-30))
            if not np.isfinite(values).all() or residual > 1e-9:
                raise CircuitSolveError(f'Numerical residual failed: {residual}.')
            residuals.append(residual)
            return values

        points, values = [], []
        if request.mode == 'dc':
            points, values = [0.0], [solve(g, factor(g), rhs())]
        elif request.mode == 'ac':
            if not request.frequencies or any(f <= 0 for f in request.frequencies):
                raise ValueError('AC analysis requires positive frequencies in Hz.')
            for frequency in request.frequencies:
                matrix = g + 2j * math.pi * frequency * c
                points.append(float(frequency))
                values.append(solve(matrix, factor(matrix), rhs(ac=True)))
        else:
            if request.stop / request.dt > 100000:
                raise ValueError('Transient exceeds the 100000-step admission limit.')
            previous = solve(g, factor(g), rhs())
            points, values = [0.0], [previous]
            # Include source discontinuities explicitly; never integrate over a
            # step event with the wrong forcing for the whole time interval.
            times = sorted(set([min(i * request.dt, request.stop) for i in range(1, math.ceil(request.stop / request.dt) + 1)]
                               + [e.step.delay for e in elements if e.step and 0 < e.step.delay < request.stop]))
            last = 0.0
            factors = {}
            for time in times:
                dt = time - last
                matrix = g + c / dt
                if dt not in factors:
                    if len(factors) >= 32:
                        factors.clear()
                    factors[dt] = factor(matrix)
                lu = factors[dt]
                forcing_time = np.nextafter(time, last)  # use the left limit at a source edge
                previous = solve(matrix, lu, rhs(forcing_time) + c @ previous / dt)
                points.append(float(time))
                values.append(previous)
                last = time

        def series(data):
            if request.mode == 'ac':
                return {'real': [float(v.real) for v in data], 'imag': [float(v.imag) for v in data],
                        'magnitude': [float(abs(v)) for v in data], 'phase_deg': [float(np.angle(v, deg=True)) for v in data]}
            return [float(v) for v in data]

        return {'ok': True, 'status': 'completed', 'mode': request.mode, 'model': 'explicit_linear_lumped',
                'axis': {'units': 'Hz' if request.mode == 'ac' else 's', 'values': points},
                'node_voltage_v': {n: series([v[i] for v in values]) for n, i in index.items()},
                'branch_current_a': {n: series([v[i] for v in values]) for n, i in branch_index.items()},
                'numerics': {'max_backward_residual': max(residuals), 'max_condition_number': max(conditions),
                             'factorizations': factorizations, 'integration': 'backward_euler' if request.mode == 'transient' else None},
                'limitations': limitations}
    except (ValidationError, ValueError, RuntimeError) as error:
        numerical = isinstance(error, (CircuitSolveError, RuntimeError))
        return {'ok': False, 'status': 'blocked', 'code': 'circuit_numerical_failure' if numerical else 'circuit_model_invalid',
                'category': 'numerical' if numerical else 'model_validation',
                'message': str(error), 'subsystem': 'circuit', 'limitations': limitations,
                'hint': 'Check explicit device models, finite values, ground paths and source constraints. No result was certified.'}
