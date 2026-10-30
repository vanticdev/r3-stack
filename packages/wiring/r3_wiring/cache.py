"""Content-addressed numeric caches under a project's generated build directory.

Cache identity includes the implementation and numeric-library versions. Arrays
are hashed at full precision; cached solver states are re-audited by the caller.
Files contain only JSON and NumPy arrays (never executable pickle payloads).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import platform
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

import numpy as np


def _update(digest: Any, value: Any) -> None:
    """Hash typed, length-delimited values without expanding numeric arrays to JSON.

    Mapping order is retained: insertion order can affect deterministic solver
    layouts. Sets are sorted because their order has no semantic meaning.
    """
    if isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        if array.dtype.hasobject:
            raise TypeError('object arrays cannot identify numeric cache entries')
        _update(digest, ('array', array.dtype.str, array.shape))
        digest.update(memoryview(array.reshape(-1)).cast('B'))
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        _update(digest, (type(value).__qualname__, [(f.name, getattr(value, f.name)) for f in dataclasses.fields(value)]))
    elif isinstance(value, dict):
        digest.update(b'dict:')
        _update(digest, list(value.items()))
    elif isinstance(value, (list, tuple)):
        digest.update(f'{type(value).__name__}:{len(value)}:'.encode())
        for item in value:
            _update(digest, item)
    elif isinstance(value, (set, frozenset)):
        digest.update(b'set:')
        _update(digest, sorted(value))
    else:
        if isinstance(value, np.generic):
            value = value.item()
        encoded = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()
        digest.update(f'{type(value).__name__}:{len(encoded)}:'.encode())
        digest.update(encoded)


@lru_cache(maxsize=1)
def implementation_key() -> str:
    """Invalidate caches after wiring code or numerical-runtime changes."""
    import scipy

    digest = hashlib.sha256()
    _update(digest, (platform.python_version(), platform.machine(), np.__version__, scipy.__version__))
    root = Path(__file__).parent
    for path in sorted(root.rglob('*.py')):
        _update(digest, (str(path.relative_to(root)), path.read_bytes().hex()))
    return digest.hexdigest()


def content_key(*values: Any) -> str:
    digest = hashlib.sha256()
    _update(digest, values)
    return digest.hexdigest()


class NumericCache:
    """Optional disk cache; missing, damaged or unwritable entries recompute normally."""

    def __init__(self, directory: Path | None, namespace: str) -> None:
        self.directory = directory / namespace if directory is not None and os.environ.get('R3_WIRING_CACHE') != '0' else None

    def key(self, *values: Any) -> str:
        return content_key(implementation_key(), *values)

    def load(self, key: str) -> tuple[dict[str, np.ndarray], dict[str, Any]] | None:
        if self.directory is None:
            return None
        try:
            with np.load(self.directory / f'{key}.npz', allow_pickle=False) as data:
                metadata = json.loads(str(data['metadata']))
                arrays = {name: data[name] for name in data.files if name != 'metadata'}
            if metadata['key'] != key or metadata['checksum'] != content_key(arrays, metadata['value']):
                return None
            return arrays, metadata['value']
        except (OSError, ValueError, KeyError, TypeError, BadZipFile, EOFError):
            return None

    def store(self, key: str, arrays: dict[str, np.ndarray], value: dict[str, Any]) -> None:
        if self.directory is None:
            return
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            metadata = {'key': key, 'value': value, 'checksum': content_key(arrays, value)}
            # A unique temporary file and atomic replace make concurrent writers safe.
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix='.npz', delete=False) as stream:
                temporary = Path(stream.name)
                np.savez(stream, **arrays, metadata=np.asarray(json.dumps(metadata)))
            temporary.replace(self.directory / f'{key}.npz')
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def shape_state(shape: Any) -> Any:
    """Geometry that affects an SDF, excluding lazily constructed spatial indexes."""
    from .geometry import Box, Cylinder, MeshShape, SweptShape

    if isinstance(shape, (Box, Cylinder)):
        return shape
    if isinstance(shape, SweptShape):
        return ('swept', [shape_state(pose) for pose in shape.shapes])
    if isinstance(shape, MeshShape):
        if shape._base is not None:
            return ('posed_mesh', shape.vertices, shape.faces, shape._pose, shape_state(shape._base))
        return ('mesh', shape.vertices, shape.faces, shape.spacing, shape._local[:2])
    raise TypeError(f'uncacheable shape: {type(shape).__name__}')
