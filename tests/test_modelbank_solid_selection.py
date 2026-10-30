import pytest

from r3_catalog.modelbank.selection import select_solids


def test_selects_intact_supplied_bodies_without_sculpting(tmp_path):
    pytest.importorskip('OCP')
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    from OCP.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
    from OCP.TopoDS import TopoDS_Compound
    from OCP.gp import gp_Pnt
    body = BRepPrimAPI_MakeBox(12., 18., 23.).Shape()
    accessory = BRepPrimAPI_MakeBox(gp_Pnt(40., 0., 0.), 3., 4., 5.).Shape()
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, body)
    builder.Add(compound, accessory)
    path = tmp_path / 'supplied.step'
    writer = STEPControl_Writer()
    writer.Transfer(compound, STEPControl_AsIs)
    writer.Write(str(path))
    original = path.read_bytes()
    selected = select_solids(path, [0])
    reader = STEPControl_Reader()
    reader.ReadFile(str(selected))
    reader.TransferRoots()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(reader.OneShape(), props)
    assert props.Mass() == pytest.approx(12 * 18 * 23, rel=1e-10)
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match='body count'):
        select_solids(path, [2])
    with pytest.raises(ValueError, match='distinct'):
        select_solids(path, [0, 0])
    second = select_solids(path, [1])
    assert second.parent != selected.parent
    reader = STEPControl_Reader()
    reader.ReadFile(str(second))
    reader.TransferRoots()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(reader.OneShape(), props)
    assert props.Mass() == pytest.approx(3 * 4 * 5, rel=1e-10)
    # Cache reuse preserves the original selection's bytes and source identity.
    before = selected.read_bytes()
    assert select_solids(path, [0]).read_bytes() == before
