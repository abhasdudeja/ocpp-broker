"""
The background writer for MongoDB writes: in order, bounded, calm and lossless during an outage, never
holding up the code that submits.
"""

import asyncio
import logging
import time

import pytest

from ocpp_broker.write_behind import WriteBehind


class Sink:
    """A database stand-in recording what was written, and able to be slow, down or picky."""

    def __init__(self):
        self.rows = []
        self.attempts = 0
        self.delay = 0.0
        self.down = False  # never answers
        self.reject = set()  # values it refuses with an error
        self.started = []

    def write(self, value):
        async def run():
            self.attempts += 1
            self.started.append(time.monotonic())
            if self.down:
                await asyncio.sleep(3600)
            if self.delay:
                await asyncio.sleep(self.delay)
            if value in self.reject:
                raise ValueError(f"refused {value}")
            self.rows.append(value)

        return run


@pytest.mark.asyncio
async def test_submitting_returns_at_once_and_the_writes_happen_in_order():
    sink, writer = Sink(), WriteBehind()
    sink.delay = 0.05
    started = time.monotonic()
    for n in range(5):
        assert writer.submit(sink.write(n), "w") is True
    assert time.monotonic() - started < 0.04, "submit does not wait for the database"
    assert sink.rows == [] and writer.pending == 5
    assert await writer.flush(5) is True
    assert sink.rows == [0, 1, 2, 3, 4]
    assert writer.stats() == {"pending": 0, "written": 5, "failed": 0, "dropped": 0, "coalesced": 0, "degraded": False}
    await writer.close()


@pytest.mark.asyncio
async def test_a_write_with_the_same_key_replaces_the_one_waiting_and_keeps_its_place():
    sink, writer = Sink(), WriteBehind()
    sink.delay = 0.05
    writer.submit(sink.write("first"), "w")
    writer.submit(sink.write("beat-1"), "heartbeat", key=("beat", "CP1"))
    writer.submit(sink.write("last"), "w")
    writer.submit(sink.write("beat-2"), "heartbeat", key=("beat", "CP1"))
    writer.submit(sink.write("beat-3"), "heartbeat", key=("beat", "CP1"))
    await writer.flush(5)
    assert sink.rows == ["first", "beat-3", "last"], "stored once, with the newest content, where the first one stood"
    assert writer.coalesced == 2
    await writer.close()


@pytest.mark.asyncio
async def test_a_write_with_a_key_that_arrives_while_that_key_is_being_written_is_written_next():
    sink, writer = Sink(), WriteBehind()
    sink.delay = 0.1
    writer.submit(sink.write("beat-1"), "heartbeat", key="beat")
    await asyncio.sleep(0.03)  # beat-1 is being written now
    writer.submit(sink.write("beat-2"), "heartbeat", key="beat")
    await writer.flush(5)
    assert sink.rows == ["beat-1", "beat-2"], "the newer content is not lost because the older was in flight"
    await writer.close()


@pytest.mark.asyncio
async def test_the_queue_is_bounded_and_drops_the_newest_with_a_count_and_one_warning(caplog):
    sink, writer = Sink(), WriteBehind(max_pending=3)
    sink.delay = 0.05
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.write_behind"):
        results = [writer.submit(sink.write(n), "w") for n in range(10)]
    assert results.count(True) == 3 and results.count(False) == 7
    assert writer.dropped == 7
    assert caplog.text.count("backing up") == 1, "one warning, not one per dropped write"
    await writer.flush(5)
    assert sink.rows == [0, 1, 2], "what was kept is the oldest, in order"
    assert writer.submit(sink.write(99), "w") is True, "there is room again"
    await writer.close()


@pytest.mark.asyncio
async def test_a_coalesced_write_needs_no_room():
    sink, writer = Sink(), WriteBehind(max_pending=1)
    sink.delay = 0.05
    assert writer.submit(sink.write(1), "w", key="k") is True
    assert writer.submit(sink.write(2), "w", key="k") is True
    assert writer.dropped == 0


@pytest.mark.asyncio
async def test_a_database_that_does_not_answer_keeps_its_writes_and_gets_them_when_it_returns(caplog):
    sink, writer = Sink(), WriteBehind(timeout=0.05, breaker=2, cooldown=0.05)
    sink.down = True
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.write_behind"):
        for n in range(3):
            writer.submit(sink.write(n), "w")
        await asyncio.sleep(0.4)
    assert sink.rows == [] and writer.pending == 3, "nothing lost while it is away"
    assert writer.failed >= 2 and writer.degraded is True
    assert caplog.text.count("did not finish") == 1, "reported once, not for each attempt"
    sink.down = False
    assert await writer.flush(3) is True
    assert sink.rows == [0, 1, 2], "everything, in the original order, once it answers"
    assert writer.degraded is False
    await writer.close()


@pytest.mark.asyncio
async def test_while_the_database_is_down_attempts_are_spaced_by_the_cooldown():
    sink, writer = Sink(), WriteBehind(timeout=0.02, breaker=1, cooldown=0.15)
    sink.down = True
    writer.submit(sink.write("x"), "w")
    await asyncio.sleep(0.6)
    assert 2 <= sink.attempts <= 5, f"not a busy loop: {sink.attempts} attempts in 0.6 s"
    gaps = [b - a for a, b in zip(sink.started, sink.started[1:])]
    assert all(g >= 0.14 for g in gaps[1:]), gaps
    sink.down = False
    await writer.flush(3)
    await writer.close()


@pytest.mark.asyncio
async def test_a_record_the_database_keeps_rejecting_is_dropped_so_it_cannot_block_the_rest():
    sink, writer = Sink(), WriteBehind(max_attempts=3, breaker=10)
    sink.reject = {"bad"}
    for value in ("a", "bad", "b"):
        writer.submit(sink.write(value), "w")
    await writer.flush(3)
    assert sink.rows == ["a", "b"]
    assert sink.attempts == 5, "a, then bad three times, then b"
    assert writer.failed == 3 and writer.written == 2 and writer.pending == 0
    await writer.close()


@pytest.mark.asyncio
async def test_a_record_that_fails_twice_and_then_works_is_written():
    writer, tries, rows = WriteBehind(max_attempts=3, breaker=10), [], []

    def flaky():
        async def run():
            tries.append(1)
            if len(tries) < 3:
                raise ValueError("not yet")
            rows.append("flaky")

        return run

    writer.submit(flaky(), "w")
    await writer.flush(3)
    assert rows == ["flaky"] and len(tries) == 3
    await writer.close()


@pytest.mark.asyncio
async def test_flush_gives_up_after_its_time_limit_and_says_so():
    sink, writer = Sink(), WriteBehind(timeout=30)
    sink.down = True
    writer.submit(sink.write("x"), "w")
    assert await writer.flush(0.1) is False
    assert await WriteBehind().flush(0.1) is True, "nothing pending: nothing to wait for"
    await writer.close(flush_timeout=0.05)


@pytest.mark.asyncio
async def test_closing_writes_what_is_waiting_then_refuses_more():
    sink, writer = Sink(), WriteBehind()
    sink.delay = 0.02
    for n in range(4):
        writer.submit(sink.write(n), "w")
    await writer.close(flush_timeout=3)
    assert sink.rows == [0, 1, 2, 3]
    assert writer.submit(sink.write(9), "w") is False
    assert sink.rows == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_closing_with_a_database_that_never_answers_does_not_hang_and_warns(caplog):
    sink, writer = Sink(), WriteBehind(timeout=30)
    sink.down = True
    writer.submit(sink.write("x"), "w")
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.write_behind"):
        await writer.close(flush_timeout=0.1)
    assert time.monotonic() - started < 2
    assert "not yet stored" in caplog.text


@pytest.mark.asyncio
async def test_the_worker_starts_when_there_is_work_and_stays_quiet_when_there_is_none():
    sink, writer = Sink(), WriteBehind()
    lane = writer._lanes[0]
    assert lane._worker is None
    writer.submit(sink.write(1), "w")
    assert lane._worker is not None
    await writer.flush(3)
    writer.submit(sink.write(2), "w")  # the same worker picks it up after being idle
    await writer.flush(3)
    assert sink.rows == [1, 2]
    await writer.close()


@pytest.mark.asyncio
async def test_many_submitters_keep_each_ones_order():
    sink, writer = Sink(), WriteBehind()

    async def producer(name):
        for n in range(20):
            writer.submit(sink.write((name, n)), "w")
            await asyncio.sleep(0)

    await asyncio.gather(*(producer(name) for name in "abc"))
    await writer.flush(5)
    for name in "abc":
        assert [n for who, n in sink.rows if who == name] == list(range(20))
    assert len(sink.rows) == 60
    await writer.close()


@pytest.mark.asyncio
async def test_different_chargers_are_written_side_by_side_and_each_chargers_records_in_order():
    sink, writer = Sink(), WriteBehind(lanes=8)
    sink.delay = 0.2
    started = time.monotonic()
    chargers = [f"CP{n}" for n in range(8)]
    for step in range(2):
        for charger in chargers:
            writer.submit(sink.write((charger, step)), "w", shard=("Org", charger))
    await writer.flush(10)
    took = time.monotonic() - started
    assert len(sink.rows) == 16
    # chargers share lanes by chance (a lane takes about two of them), so allow for that: still far below one at a time
    assert took < 2.2, f"{took:.2f} s: 16 writes of 0.2 s each would take 3.2 s one after the other"
    for charger in chargers:
        assert [step for who, step in sink.rows if who == charger] == [0, 1], "one charger's records stay in order"
    await writer.close()


@pytest.mark.asyncio
async def test_one_shard_always_uses_the_same_lane_and_no_shard_uses_the_first():
    writer = WriteBehind(lanes=4)
    sink = Sink()
    sink.delay = 0.05
    for n in range(6):
        writer.submit(sink.write(n), "w", shard="same")
    used = [i for i, lane in enumerate(writer._lanes) if lane.pending]
    assert len(used) == 1
    writer.submit(sink.write("x"), "w")
    assert writer._lanes[0].pending >= 1
    await writer.flush(5)
    await writer.close()


@pytest.mark.asyncio
async def test_the_statistics_add_up_across_lanes_and_one_failing_lane_makes_the_queue_degraded():
    writer = WriteBehind(lanes=3, timeout=0.05, breaker=1, cooldown=0.05)
    good, bad = Sink(), Sink()
    bad.down = True
    for n in range(3):
        writer.submit(good.write(n), "w", shard=("Org", "A"))
    writer.submit(bad.write("x"), "w", shard=("Org", "B"))
    await asyncio.sleep(0.3)
    stats = writer.stats()
    assert stats["written"] == 3 and stats["failed"] >= 1 and stats["pending"] == 1
    assert stats["degraded"] is True and writer.degraded is True
    bad.down = False
    assert await writer.flush(3) is True
    assert writer.degraded is False
    await writer.close()


@pytest.mark.asyncio
async def test_closing_closes_every_lane():
    writer, sink = WriteBehind(lanes=3), Sink()
    for n in range(6):
        writer.submit(sink.write(n), "w", shard=n)
    await writer.close(flush_timeout=3)
    assert sorted(sink.rows) == list(range(6))
    assert all(lane._closed for lane in writer._lanes)
    assert writer.submit(sink.write("late"), "w", shard=1) is False
