"""Near-antiparallel router edges remain a reported bend, never a metre-scale arc."""
from types import SimpleNamespace

import numpy as np
import pytest

from r3_wiring.router import HarnessRouter, RouterOptions


@pytest.mark.parametrize('offset', [0., 1e-10, 1e-8, 1e-6])
def test_foldback_stays_local_and_retains_bend_failure(offset):
    router = HarnessRouter.__new__(HarnessRouter)
    router.opt = RouterOptions()
    router._hard_clear = lambda *args: True
    points = np.array([[0., 0., 0.], [.03, 0., 0.], [0., offset, 0.]])
    node = SimpleNamespace(kind='free', termination_length=0., exempt=set())
    path, _, _, bends = router._fillet(points, np.full(3, .00165), np.ones(3), np.full(3, .0132),
                                      node, node, set())
    assert np.isfinite(path).all()
    assert np.linalg.norm(path, axis=1).max() <= .030001
    assert len(bends) == 1 and bends[0].radius == 0.
    assert bends[0].required == pytest.approx(.0132)
