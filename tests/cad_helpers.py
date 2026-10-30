"""Build exact synthetic geometry locally from the same standard part plans as CAD."""
from r3_model.project import Project
from r3_onshape.model import GeometryModel
from r3_onshape.organized import plan_assembly, transform_part
from r3_onshape.parts import build_part_catalog
from r3_onshape.recompose import part_from_plan


def geometry_for(project: Project):
    parts, instances = build_part_catalog(project)
    from r3_onshape.parts import instance_poses
    poses = instance_poses(project)
    geometry = {name: transform_part(part_from_plan(name, parts[key].plan), poses[name], name)
                for name, key in instances.items()}
    materials = {name: parts[key].plan.material for name, key in instances.items()}
    return GeometryModel(geometry, source="generated synthetic coupon geometry"), materials
