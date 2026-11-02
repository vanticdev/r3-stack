"""Bounded, content-addressed numeric work cache; never a CAD or release authority."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import tempfile
from functools import lru_cache
from typing import Any
import zipfile

import numpy as np
import scipy


def _hash_value(digest: Any, value: Any) -> None:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        _hash_value(digest, ('array', array.dtype.str, array.shape))
        digest.update(memoryview(array).cast('B'))
    elif isinstance(value, (bytes, bytearray)):
        digest.update(b'bytes' + str(len(value)).encode() + b':' + value)
    else:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        digest.update(str(len(encoded)).encode() + b':' + encoded)


@lru_cache(maxsize=1)
def kernel_identity() -> str:
    """Invalidate derived data after algorithm or numerical-library changes."""
    digest = hashlib.sha256()
    for name in ('cache.py', 'geometry.py', 'grid.py', 'router.py'):
        digest.update(Path(__file__).with_name(name).read_bytes())
    _hash_value(digest, (np.__version__, scipy.__version__, platform.python_version(), platform.system(), platform.machine()))
    return digest.hexdigest()


def shape_identity(shape: Any) -> str | None:
    from .geometry import Box, Cylinder, MeshShape, SweptShape
    digest = hashlib.sha256()
    if type(shape) is Box:
        values = ('box', shape.center, shape.rotation, shape.half)
    elif type(shape) is Cylinder:
        values = ('cylinder', shape.center, shape.rotation, shape.radius, shape.half_length)
    elif type(shape) is MeshShape:
        values = ['mesh', shape.vertices, shape.faces, shape.spacing]
        if shape._base is not None:
            base = shape_identity(shape._base)
            if base is None:
                return None
            values.extend([base, *shape._pose])
        else:
            # Explicit surface samples are supported by MeshShape. Hash those
            # actually used, without constructing its KD-tree or changing them.
            values.extend([shape._samples, shape._normals])
    elif type(shape) is SweptShape:
        children = [shape_identity(child) for child in shape.shapes]
        if any(child is None for child in children):
            return None
        values = ('swept', children)
    else:
        return None  # Unknown implementations must compute, never guess a key.
    for value in values:
        _hash_value(digest, value)
    return digest.hexdigest()


class NumericCache:
    """One-dimensional arrays, verified on read; failures always recompute.

    Atomic files contain no executable pickle data. Only this cache's hex-named
    regular files are eligible for bounded eviction. Cache hits never replace
    native provenance validation or downstream engineering checks.
    """
    def __init__(self, root: Path, *, max_bytes: int = 512 * 1024 * 1024,
                 max_entries: int = 4096) -> None:
        self.root = Path(root)
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.stats: dict[str, int] = {}
        self.enabled = False
        try:
            if self.root.resolve() != self.root.absolute():
                return  # Do not create or evict files through a symlinked directory.
            self.root.mkdir(parents=True, exist_ok=True)
            marker = self.root / '.r3-wiring-cache-v1'
            if marker.is_symlink():
                return
            if not marker.exists():
                if any(self.root.iterdir()):
                    return  # An existing unrelated directory is never an eviction target.
                marker.write_text('R3 derived numeric work cache v1\n')
            self.enabled = True
            self._prune()
        except OSError:
            self.stats['unavailable'] = 1

    def key(self, kind: str, *values: Any) -> str:
        digest = hashlib.sha256()
        for value in (kernel_identity(), kind, *values):
            _hash_value(digest, value)
        return digest.hexdigest()

    def _count(self, name: str) -> None:
        self.stats[name] = self.stats.get(name, 0) + 1

    def _path(self, key: str) -> Path:
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('Expected a content-addressed cache key')
        return self.root / (key + '.npz')

    def get(self, kind: str, key: str, *, size: int | None, dtype: Any) -> np.ndarray | None:
        path = self._path(key)
        if self.enabled and path.is_file() and not path.is_symlink():
            try:
                with zipfile.ZipFile(path) as archive:
                    if set(archive.namelist()) != {'value.npy', 'checksum.npy', 'key.npy'}:
                        raise ValueError('Cache archive contract mismatch')
                    if sum(info.file_size for info in archive.infolist()) > self.max_bytes:
                        raise ValueError('Cache expansion exceeds budget')
                    with archive.open('value.npy') as stream:
                        version = np.lib.format.read_magic(stream)
                        if version != (1, 0):
                            raise ValueError('Unsupported numeric cache header')
                        shape, fortran, actual_dtype = np.lib.format.read_array_header_1_0(stream)
                        if (len(shape) != 1 or fortran or actual_dtype != np.dtype(dtype)
                                or shape[0] * actual_dtype.itemsize > self.max_bytes
                                or (size is not None and shape != (size,))):
                            raise ValueError('Cache array header mismatch')
                with np.load(path, allow_pickle=False) as data:
                    if set(data.files) != {'value', 'checksum', 'key'} or str(data['key'].item()) != key:
                        raise ValueError('Cache identity mismatch')
                    value = data['value']
                    if (value.ndim != 1 or value.dtype != np.dtype(dtype)
                            or (size is not None and len(value) != size) or not np.isfinite(value).all()):
                        raise ValueError('Cache array contract mismatch')
                    checksum = hashlib.sha256(memoryview(np.ascontiguousarray(value)).cast('B')).hexdigest()
                    if checksum != str(data['checksum'].item()):
                        raise ValueError('Cache checksum mismatch')
                try:
                    path.touch()
                except OSError:
                    pass
                self._count(kind + '_hits')
                return value
            except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
                self._count('corrupt')
        self._count(kind + '_misses')
        return None

    def put(self, key: str, value: np.ndarray) -> None:
        if not self.enabled:
            return
        value = np.ascontiguousarray(value)
        if (value.ndim != 1 or value.dtype.kind not in 'fi' or value.nbytes > self.max_bytes
                or not np.isfinite(value).all()):
            return
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=self.root)
            with os.fdopen(fd, 'wb') as file:
                checksum = hashlib.sha256(memoryview(value).cast('B')).hexdigest()
                np.savez_compressed(file, value=value, checksum=checksum, key=key)
            if Path(temporary).stat().st_size <= self.max_bytes:
                os.replace(temporary, self._path(key))
                self._count('writes')
                self._prune()
        except OSError:
            self._count('write_failures')
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass  # Cache cleanup cannot invalidate physical routing.

    def _prune(self) -> None:
        files = []
        for path in self.root.iterdir():
            if (len(path.stem) != 64 or path.suffix != '.npz'
                    or any(c not in '0123456789abcdef' for c in path.stem)):
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                files.append((info.st_mtime_ns, info.st_size, path))
        total = sum(size for _, size, _ in files)
        count = len(files)
        for _, size, path in sorted(files):
            if total <= self.max_bytes and count <= self.max_entries:
                break
            path.unlink(missing_ok=True)
            total -= size
            count -= 1
            self._count('evictions')
