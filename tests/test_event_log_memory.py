"""Long sessions keep complete replay while bounding retained event payloads."""
import asyncio
import json

import pytest

from r3_orchestrator.api.events import EventLog, stream


@pytest.mark.parametrize('memory_limit', [0, 100, 100000])
def test_replay_survives_spill_and_close(memory_limit):
    log = EventLog(memory_limit=memory_limit)
    expected = [log.emit('message', text='payload' * 100) for _ in range(25)]
    log.close()
    assert len(log) == 25
    assert log.since() == expected
    assert log.since(8, limit=3) == expected[8:11]
    assert log.since(-1) == expected
    assert log.since(99) == []
    if memory_limit < 100000:
        assert log._events == []
        assert log._disk is not None
    with pytest.raises(RuntimeError, match='closed'):
        log.emit('late')


def test_spilled_sse_reconnect_drains_multiple_batches():
    log = EventLog(memory_limit=0)
    expected = [log.emit('message', text=str(i)) for i in range(350)]
    log.close()

    async def collect():
        return [chunk async for chunk in stream(log, after=17)]

    chunks = asyncio.run(collect())
    events = [json.loads(chunk.decode().split('data: ', 1)[1]) for chunk in chunks if b'data: ' in chunk]
    assert events == expected[17:]


def test_concurrent_emitters_keep_sequence_across_spill():
    from concurrent.futures import ThreadPoolExecutor
    log = EventLog(memory_limit=512)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: log.emit('message', number=i), range(300)))
    events = log.since()
    assert [event['seq'] for event in events] == list(range(1, 301))
    assert {event['number'] for event in events} == set(range(300))
    assert log._events == []
