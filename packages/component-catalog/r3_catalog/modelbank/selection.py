"""Select intact supplied STEP solids through Open CASCADE's native reader/writer."""
from __future__ import annotations

import hashlib
from pathlib import Path


def select_solids(path: Path, indices: list[int]) -> Path:
    """Keep complete bodies, e.g. a housing without a detachable accessory.

    No cuts, scaling, healing or authored geometry are permitted here. The
    source remains unchanged; the selection cache is keyed by its actual bytes.
    """
    from OCP.BRep import BRep_Builder
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Compound

    if not indices or min(indices) < 0 or len(indices) != len(set(indices)):
        raise ValueError('select distinct nonnegative solid indices')
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError('cannot read supplied STEP for solid selection')
    explorer = TopExp_Explorer(reader.OneShape(), TopAbs_SOLID)
    solids = []
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    if max(indices) >= len(solids):
        raise ValueError(f'solid selection {indices} exceeds supplied body count {len(solids)}')
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for index in indices:
        builder.Add(compound, solids[index])
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()[:16]
    directory = path.parent / f'selected-{digest}-{"-".join(map(str, indices))}'
    directory.mkdir(exist_ok=True)
    target = directory / 'model.step'
    checksum_file = directory / 'sha256'
    if target.exists() and checksum_file.exists():
        with target.open('rb') as stream:
            cached = hashlib.file_digest(stream, 'sha256').hexdigest()
        if cached == checksum_file.read_text().strip():
            return target
    writer = STEPControl_Writer()
    if writer.Transfer(compound, STEPControl_AsIs) != IFSelect_RetDone or writer.Write(str(target)) != IFSelect_RetDone:
        raise ValueError('native STEP export of selected supplied solids failed')
    with target.open('rb') as stream:
        checksum_file.write_text(hashlib.file_digest(stream, 'sha256').hexdigest())
    return target
