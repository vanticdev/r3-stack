"""Content revision of catalog inputs and the selected purchased-model dataset."""
from __future__ import annotations

import hashlib
from pathlib import Path


def catalog_modelbank_revision(*extra_dirs: Path) -> str:
    """Hash bytes on disk, including local overrides and model review evidence.

    Paths are relative to each input root: moving a checkout or touching a file
    cannot invalidate evidence. Read afresh so an already loaded Project notices
    edits, additions and deletions without depending on catalog/index caches.
    """
    from . import DATA_DIR
    from .modelbank import ModelBank

    digest = hashlib.sha256()
    roots = (DATA_DIR, *extra_dirs, ModelBank().dataset)
    for index, root in enumerate(roots):
        root = Path(root)
        for path in sorted(root.rglob('*.yaml')):
            name = f'{index}/{path.relative_to(root).as_posix()}'.encode()
            content = path.read_bytes()
            # Length-prefix both fields to avoid ambiguous concatenations.
            for value in (name, content):
                digest.update(len(value).to_bytes(8, 'big'))
                digest.update(value)
    return digest.hexdigest()
