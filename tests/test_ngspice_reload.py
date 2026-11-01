"""Source identity survives native circuit reloads and reused C-string storage."""
import ctypes

import pytest

from r3_sim.electrical.ngspice import NgSpice, find_library


@pytest.mark.skipif(find_library() is None, reason='libngspice not installed')
@pytest.mark.parametrize('callback', ['_on_vsrc', '_on_isrc'])
def test_reloaded_circuit_does_not_reuse_previous_source_identity(callback):
    ng = NgSpice.acquire()
    try:
        # ngspice owns these strings and may recycle their addresses between
        # circuits. Reuse one allocation deliberately to make that deterministic.
        name = ctypes.create_string_buffer(b'old_source')
        address = ctypes.addressof(name)
        value = ctypes.c_double()
        ng.source_values = {'old_source': 1.0, 'new_source': 2.0}
        getattr(ng, callback)(ctypes.pointer(value), 0, address, 0, 0)
        assert value.value == 1.0

        ng.load(['Synthetic reload', 'vnew out 0 dc 2', 'rload out 0 1000', '.end'])
        name.value = b'new_source'
        getattr(ng, callback)(ctypes.pointer(value), 0, address, 0, 0)
        assert value.value == 2.0
    finally:
        ng.release()
