"""Content-addressed model cache and conversion through OpenCASCADE (cascadio)."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
from urllib.parse import urlsplit
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .bank import cache_root
from .schema import ModelEntry, Source, Validation

MAX_BYTES = 256 * 1024 * 1024


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def checksum(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url: str, target: Path) -> Path:
    parsed = urlsplit(url)
    if parsed.scheme == 'file' and 'R3_LOCAL_READ_SCOPE' in os.environ:
        from r3_model.read_scope import local_source

        if parsed.netloc not in ('', 'localhost'):
            raise ValueError('local model source must use a local file URL')
        source = local_source(Path(urllib.request.url2pathname(parsed.path)))
        url = source.as_uri()
    request = urllib.request.Request(url, headers={'User-Agent': 'R3-modelbank/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response, target.open('wb') as out:
        size = 0
        while block := response.read(1 << 20):
            size += len(block)
            if size > MAX_BYTES:
                raise ValueError('model exceeds 256 MiB download limit')
            out.write(block)
    return target


def acquire(source: Source, *, pin: bool = False, downloader: Callable[..., Path] = download) -> tuple[Path, Source]:
    if source.requires_login or not source.direct:
        raise ValueError('source is a human-fetch pointer; no automatic download permitted')
    if source.kind == 'onshape_public':
        raise ValueError('Onshape public models are inserted by immutable version; use the CAD importer')
    if not source.sha256 and not pin:
        raise ValueError('source has no checksum: add/ingest it first to pin the download')
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    suffix = '.zip' if source.format == 'stp.zip' else '.rar' if source.format == 'stp.rar' else '.' + source.format
    if source.sha256:
        existing = root / source.sha256 / ('source' + suffix)
        if existing.exists() and checksum(existing) == source.sha256:
            return existing, source
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        path = downloader(source.url, Path(temporary) / ('source' + suffix))
        if pin and source.format in ('step', 'stp') and b'ISO-10303-21' not in path.read_bytes()[:4096]:
            raise ValueError('download is not a STEP file (possibly a login or error page)')
        if pin and source.format == 'stp.zip' and not zipfile.is_zipfile(path):
            raise ValueError('download is not a STEP ZIP archive')
        if pin and source.format == 'stp.rar':
            import rarfile
            if not rarfile.is_rarfile_sfx(path):
                raise ValueError('download is not a STEP RAR archive')
        actual = checksum(path)
        if source.sha256 and actual != source.sha256:
            raise ValueError(f'sha256 mismatch: {actual} != {source.sha256}')
        pinned = source.model_copy(update={'sha256': actual, 'retrieved_at': source.retrieved_at or timestamp()})
        target = root / actual / ('source' + suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        return target, pinned


def unpack(path: Path, source: Source) -> Path:
    if source.solid_indices:
        from .selection import select_solids

        original = unpack(path, source.model_copy(update={'solid_indices': []}))
        return select_solids(original, source.solid_indices)
    if source.format == 'stp.rar':
        return unpack_rar(path, source)
    if source.format != 'stp.zip':
        return path
    with zipfile.ZipFile(path) as archive:
        members = [m for m in archive.infolist() if not m.is_dir() and _step_member(m.filename)]
        if source.member:
            members = [m for m in members if m.filename == source.member]
        if len(members) != 1:
            raise ValueError('archive has zero or multiple STEP models; specify source.member')
        member = members[0]
        if member.file_size > MAX_BYTES:
            raise ValueError('uncompressed STEP exceeds 256 MiB')
        target = path.parent / 'model.step'
        # Fixed basename: never extract an archive-controlled path.
        target.write_bytes(archive.read(member))
        return target


def _step_member(name: str) -> bool:
    """Ignore macOS archive metadata, whose resource forks reuse CAD suffixes."""
    parts = name.replace('\\', '/').split('/')
    return (name.lower().endswith(('.step', '.stp'))
            and '__MACOSX' not in parts and not any(part.startswith('._') for part in parts))


def unpack_rar(path: Path, source: Source) -> Path:
    """Read one bounded STEP member to a fixed name; never extract archive paths.

    rarfile supports the vendor's HTML-prefixed RAR response (an SFX archive).
    Compressed members need unrar, unar, bsdtar or 7z on the worker.
    """
    import rarfile

    try:
        archive = rarfile.RarFile(path)
    except rarfile.Error as error:
        raise ValueError(f'invalid STEP RAR archive: {error}') from error
    with archive:
        members = [m for m in archive.infolist() if not m.isdir() and _step_member(m.filename)]
        if source.member:
            members = [m for m in members if m.filename == source.member]
        if len(members) != 1:
            raise ValueError('archive has zero or multiple STEP models; specify source.member')
        member = members[0]
        if member.file_size > MAX_BYTES:
            raise ValueError('uncompressed STEP exceeds 256 MiB')
        try:
            with archive.open(member) as stream:
                data = stream.read(MAX_BYTES + 1)
        except rarfile.Error as error:
            raise ValueError(f'RAR extraction failed (install bsdtar/unar/unrar/7z): {error}') from error
        if len(data) > MAX_BYTES:
            raise ValueError('uncompressed STEP exceeds 256 MiB')
        target = path.parent / 'model.step'
        target.write_bytes(data)
        return target


def convert(path: Path, source: Source) -> dict[str, Any]:
    import trimesh

    model = unpack(path, source)
    if source.format in ('step', 'stp', 'stp.zip', 'stp.rar'):
        if b'ISO-10303-21' not in model.read_bytes()[:4096]:
            raise ValueError('download is not a STEP file')
        from r3_connectors.models import BOARD_TOLERANCE, step_to_glb
        glb = step_to_glb(model, BOARD_TOLERANCE)
        scene = trimesh.load(glb, force='scene')
    else:
        if source.format == 'wrl':
            executable = shutil.which('assimp')
            if not executable:
                raise ValueError('VRML conversion needs the Assimp CLI; install assimp or use the matching STEP')
            converted = path.parent / 'vrml.glb'
            try:
                process = subprocess.run([executable, 'export', str(model), str(converted), '-fglb2'],
                                         capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired as error:
                raise ValueError('Assimp VRML conversion exceeded 180 seconds') from error
            if process.returncode or not converted.exists():
                raise ValueError(f'Assimp VRML conversion failed: {process.stderr[-500:]}')
            scene = trimesh.load(converted, force='scene')
            if source.kind == 'kicad_3d':
                scene.apply_scale(0.00254)  # KiCad's legacy VRML library units are 0.1 inch.
        else:
            scene = trimesh.load(model, force='scene')
    if not scene.geometry:
        raise ValueError('model contains no tessellated geometry')
    mesh = scene.to_mesh()
    output = model.parent if source.solid_indices else path.parent
    glb_target, stl_target = output / 'model.glb', output / 'model.stl'
    scene.export(glb_target)
    mesh.export(stl_target)
    return {'source': str(model), 'glb': str(glb_target), 'stl': str(stl_target), 'bbox_m': mesh.extents.tolist(),
            'bounds_m': mesh.bounds.tolist(), 'sha256': checksum(path)}


def fetch(entry: ModelEntry, *, converter: Callable[..., dict[str, Any]] = convert,
          downloader: Callable[..., Path] = download) -> dict[str, Any]:
    order = {'onshape_public': 0, 'vendor_step': 1, 'kicad_3d': 2, 'database': 3, 'local': 4}
    failures = []
    for source in sorted(entry.sources, key=lambda s: order[s.kind]):
        if source.kind == 'onshape_public':
            failures.append({'url': source.url, 'reason': 'insert public version through CAD; no standalone download'})
            continue
        try:
            path, pinned = acquire(source, downloader=downloader)
            return {'ok': True, 'id': entry.id, 'provenance': pinned.model_dump(), **converter(path, pinned)}
        except (OSError, ValueError, RuntimeError, ImportError) as error:
            failures.append({'url': source.url, 'reason': str(error)})
    return {'ok': False, 'id': entry.id, 'code': 'model_unavailable', 'message': 'No downloadable, checksum-pinned model', 'sources': failures}


def validate(entry: ModelEntry, bbox_m: list[float], features: dict[str, bool] | None = None,
             who: str = 'r3/cascadio') -> Validation:
    expected = entry.key_specs.get('dimensions')
    expected_box = expected.value if expected and isinstance(expected.value, list) else entry.validation.expected_bbox_m
    feature_checks = dict(entry.validation.key_features)
    feature_checks.update(features or {})
    matches = expected_box is not None and all(abs(a - b) <= entry.validation.tolerance_m for a, b in zip(bbox_m, expected_box))
    status = 'mismatch' if expected_box and not matches or any(not found for found in feature_checks.values()) else 'unverified'
    # A bounding box cannot prove the location of mounting holes or interface frames.
    if matches and feature_checks and all(feature_checks.values()):
        status = 'passed'
    return Validation(status=status, bbox_m=bbox_m, expected_bbox_m=expected_box, key_features=feature_checks,
                      tolerance_m=entry.validation.tolerance_m, date=timestamp(), validated_by=who,
                      notes='Axis-specific bbox check; interface features require independent geometry inspection.')
