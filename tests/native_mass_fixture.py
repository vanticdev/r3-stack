"""Explicit fake Onshape output for a uniform, unrelated rectangular coupon."""
import numpy as np


def uniform_box(density=1000.):
    size=np.array([.01,.02,.03])
    volume=float(np.prod(size));mass=density*volume
    inertia=np.diag(mass*(np.sum(size**2)-size**2)/12)
    def bounds(value):
        return np.tile(np.asarray(value).reshape(-1),3).tolist()
    return {'mass':bounds([mass]),'volume':bounds([volume]),'centroid':bounds(size/2),
            'inertia':bounds(inertia),'hasMass':True,'massMissingCount':0}
