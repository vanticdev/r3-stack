"""Bounded native MJB work cache; never a CAD or qualification authority."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from functools import lru_cache

import mujoco
from r3_model.project import Project

_LOG = logging.getLogger(__name__)
_MARKER = 'R3 compiled MuJoCo models v1\n'
_NAME = re.compile(r'^[0-9a-f]{64}\.json$')


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _runtime_identity() -> dict[str, str]:
    root = Path(mujoco.__file__).parent
    return {str(p.relative_to(root)): _sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.suffix in ('.so', '.dylib', '.dll')}


def _inputs(xml: str) -> dict[str, object] | None:
    """Only cache dependencies whose exact bytes are known before compilation."""
    tree = ET.fromstring(xml)
    if tree.findall('.//include'):
        return None  # Let MuJoCo resolve includes; never guess their dependencies.
    compiler = tree.find('compiler')
    if compiler is not None and compiler.get('strippath') == 'true':
        return None  # Absolute filenames can be redirected by this compiler option.
    if any(p.get('plugin') not in (None, 'mujoco.elasticity.cable') for p in tree.findall('.//plugin')):
        return None  # External plugin binaries are not covered by the bundled runtime identity.
    assets = {}
    for element in tree.iter():
        if name := element.get('file'):
            path = Path(name)
            if not path.is_absolute():
                return None  # Native compiler meshdir/texturedir rules remain authoritative.
            assets[str(path)] = _sha(path)
    return {'xml_sha256': hashlib.sha256(xml.encode()).hexdigest(), 'assets': assets,
            'mujoco_version': mujoco.__version__, 'machine': platform.machine(),
            'system': platform.system(), 'runtime_binaries': _runtime_identity(), 'cache_version': 1}


def _compile(xml: str) -> mujoco.MjModel:
    started = time.monotonic()
    _LOG.info('Compiling native MuJoCo model')
    model = mujoco.MjModel.from_xml_string(xml)
    _LOG.info('Native MuJoCo compilation complete in %.1f s', time.monotonic() - started)
    return model


class CompiledModels:
    """Fresh mutable models loaded from checksum-verified, input-addressed MJBs.

    The native compiler writes the binary before any runtime mutations. Every
    hit loads a separate model; native CAD validation and all physics checks
    still run. Uncached/unsupported inputs always use the ordinary compiler.
    """

    def __init__(self, root: Path, *, max_bytes: int = 1024 ** 3, max_entries: int = 4) -> None:
        self.root, self.max_bytes, self.max_entries = Path(root), max_bytes, max_entries

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        root = self.root
        if root.resolve() != root.absolute():
            raise OSError('Model cache path contains a symlink')
        root.mkdir(parents=True, exist_ok=True)
        marker = root / '.r3-mujoco-model-cache-v1'
        if not marker.exists():
            if any(root.iterdir()):
                raise OSError('Existing unrelated directory is not a model cache')
            marker.write_text(_MARKER)
        if marker.is_symlink() or marker.read_text() != _MARKER:
            raise OSError('Invalid model cache marker')
        fd = os.open(root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for path in root.glob('.native-model-*'):
                if path.is_file() and not path.is_symlink():
                    path.unlink()  # Interrupted writes in this explicitly owned cache.
            for receipt in root.glob('*.json'):
                if _NAME.fullmatch(receipt.name) and not receipt.is_symlink() and not receipt.with_suffix('.mjb').is_file():
                    receipt.unlink()
            for binary in root.glob('*.mjb'):
                if _NAME.fullmatch(binary.with_suffix('.json').name) and not binary.is_symlink() and not binary.with_suffix('.json').is_file():
                    binary.unlink()
            yield

    def load(self, xml: str) -> mujoco.MjModel:
        if os.environ.get('R3_MUJOCO_MODEL_CACHE') == '0':
            return _compile(xml)
        inputs = _inputs(xml)
        if inputs is None:
            return _compile(xml)
        key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        with contextlib.ExitStack() as stack:
            try:
                stack.enter_context(self._locked())
            except OSError as error:
                _LOG.info('MuJoCo work cache unavailable: %s', error)
                return _compile(xml)
            model = self._read(key, inputs)
            if model is not None:
                if _inputs(xml) != inputs:
                    raise RuntimeError('MuJoCo source assets changed while loading the model')
                _LOG.info('Reused unchanged native MuJoCo model %s', key[:12])
                return model
            model = _compile(xml)  # Compiler/physics errors propagate; never retry them as cache failures.
            if _inputs(xml) != inputs:
                raise RuntimeError('MuJoCo source assets changed during compilation')
            try:
                self._write(key, inputs, model)
            except (OSError, ValueError, mujoco.FatalError) as error:
                _LOG.info('MuJoCo work cache could not be written: %s', error)
            return model

    def _read(self, key: str, inputs: dict[str, object]) -> mujoco.MjModel | None:
        receipt, binary = self.root / f'{key}.json', self.root / f'{key}.mjb'
        try:
            if receipt.is_symlink() or binary.is_symlink() or receipt.stat().st_size > 2 * 1024 ** 2:
                return None
            record = json.loads(receipt.read_text())
            if (record['inputs'] != inputs or binary.stat().st_size != record['bytes']
                    or record['bytes'] > self.max_bytes or _sha(binary) != record['sha256']):
                return None
            model = mujoco.MjModel.from_binary_path(str(binary))
            os.utime(receipt, follow_symlinks=False)
            return model
        except (OSError, ValueError, TypeError, KeyError, mujoco.FatalError):
            return None

    def _write(self, key: str, inputs: dict[str, object], model: mujoco.MjModel) -> None:
        if mujoco.mj_sizeModel(model) > self.max_bytes or self.max_entries <= 0:
            return
        fd, name = tempfile.mkstemp(prefix='.native-model-', dir=self.root)
        os.close(fd)
        temporary = Path(name)
        receipt_temporary = None
        try:
            mujoco.mj_saveModel(model, str(temporary))
            record = {'inputs': inputs, 'sha256': _sha(temporary), 'bytes': temporary.stat().st_size}
            temporary.replace(self.root / f'{key}.mjb')
            # Atomic replacement also avoids following an old receipt symlink.
            fd, receipt_name = tempfile.mkstemp(prefix='.native-model-', dir=self.root)
            receipt_temporary = Path(receipt_name)
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream, sort_keys=True)
            receipt_temporary.replace(self.root / f'{key}.json')
            records = sorted((p for p in self.root.iterdir() if _NAME.fullmatch(p.name) and not p.is_symlink()),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            total = 0
            for index, receipt in enumerate(records):
                binary = receipt.with_suffix('.mjb')
                if binary.is_symlink() or not binary.is_file():
                    continue
                total += binary.stat().st_size
                if index >= self.max_entries or total > self.max_bytes:
                    binary.unlink()
                    receipt.unlink()
        finally:
            temporary.unlink(missing_ok=True)
            if receipt_temporary is not None:
                receipt_temporary.unlink(missing_ok=True)


def compile_model(project: Project, xml: str) -> mujoco.MjModel:
    """Cache only derived native compilation; callers still validate the pull."""
    return CompiledModels(project.build_dir('cache/mujoco')).load(xml)
