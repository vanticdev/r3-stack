"""Explicit fake Onshape mass output for unrelated synthetic geometry."""
import numpy as np


def _response(mass, volume, center, inertia):
    def bounds(value):
        return np.tile(np.asarray(value).reshape(-1), 3).tolist()
    return {'mass': bounds([mass]), 'volume': bounds([volume]), 'centroid': bounds(center),
            'inertia': bounds(inertia), 'hasMass': True, 'massMissingCount': 0}


def uniform_box(density=1000.):
    size = np.array([.01, .02, .03])
    volume = float(np.prod(size)); mass = density * volume
    inertia = np.diag(mass * (np.sum(size**2) - size**2) / 12)
    return _response(mass, volume, size / 2, inertia)


def uniform_mesh(mesh, density):
    """Independent triangle integrals supply a compound fixture's API stub."""
    values = mesh.mass_properties
    return _response(values.volume * density, values.volume, values.center_mass,
                     values.inertia * density / values.density)
