"""Read synthetic STEP fixture hand-offs, with no geometry generator imports."""
from r3_model.pulled import load_pulled
from r3_onshape.step_geometry import geometry_from_pulled


def geometry_for(project):
    pulled = load_pulled(project.root)
    return geometry_from_pulled(pulled), {name: part.material or "" for name, part in pulled.parts.items()}
