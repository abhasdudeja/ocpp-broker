"""
The event bus and the SSE text it is turned into (no server): ordering, filtering, catching up after a
reconnect, and above all that a slow or broken listener can never hold up the code that publishes.
"""

import asyncio
import json
import pathlib
import re
import typing

import pytest

from ocpp_broker.events import EVENT_TYPES, BusClosed, EventBus, StreamRefused, TooManyListeners
from ocpp_broker.events_api import MAX_REPLAY, event_stream, sse_message
from ocpp_broker.schemas.console import ConsoleEvent


def publish_many(bus, count, org="Fleet", charger_id="CP1", type="charger.status"):
    return [bus.publish(type, org, charger_id, n=n) for n in range(count)]


# ---------------------------------------------------------------------------
# The bus
# ---------------------------------------------------------------------------
def test_events_are_numbered_from_one_and_carry_what_was_published():
    bus = EventBus()
    first = bus.publish("charger.connected", "Fleet", "CP1", mode="relay")
    second = bus.publish("backend.link", "Fleet", "CP1", backend="primary", connected=False)
    assert (first.id, second.id) == (1, 2) and bus.last_id == 2
    assert (first.type, first.org, first.charger_id, first.data) == ("charger.connected", "Fleet", "CP1", {"mode": "relay"})
    assert first.time.tzinfo is not None


def test_the_event_types_the_api_documents_are_the_ones_that_can_be_published():
    assert set(typing.get_args(ConsoleEvent.model_fields["type"].annotation)) == set(EVENT_TYPES)
    assert len(set(EVENT_TYPES)) == len(EVENT_TYPES)


def test_the_api_reference_lists_exactly_the_event_types_there_are():
    text = (pathlib.Path(__file__).resolve().parents[1] / "docs" / "api-reference.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z]+\.[a-z]+)` \|", text, flags=re.MULTILINE))
    assert documented == set(EVENT_TYPES)


@pytest.mark.asyncio
async def test_a_listener_gets_what_matches_it_in_order():
    bus = EventBus()
    everything = bus.subscribe()
    fleet = bus.subscribe(org="Fleet")
    one = bus.subscribe(org="Fleet", charger_id="CP1")
    bus.publish("a", "Fleet", "CP1")
    bus.publish("b", "Fleet", "CP2")
    bus.publish("c", "Home", "CP1")
    bus.publish("d", None, None)

    async def drain(listener):
        got = []
        while (event := await listener.next(0.01)) is not None:
            got.append(event.type)
        return got

    assert await drain(everything) == ["a", "b", "c", "d"]
    assert await drain(fleet) == ["a", "b"]
    assert await drain(one) == ["a"], "a charger id alone does not match another organization's charger of the same name"


@pytest.mark.asyncio
async def test_a_listener_that_falls_behind_is_cut_off_and_publishing_never_waits():
    bus = EventBus(queue_size=3)
    slow = bus.subscribe()
    keeping_up = bus.subscribe()
    started = asyncio.get_running_loop().time()
    for n in range(50):
        bus.publish("charger.status", "Fleet", "CP1", n=n)
        await keeping_up.next(0.01)
    assert asyncio.get_running_loop().time() - started < 1, "a full queue must not make publish() wait"
    assert slow.overflowed and not keeping_up.overflowed
    # what the slow listener had already queued is still delivered, then it is told it is finished
    delivered = []
    while (event := await slow.next(0.01)) is not None:
        delivered.append(event.data["n"])
    assert delivered == [0, 1, 2] and slow.overflowed


def test_a_listener_that_breaks_is_dropped_and_the_others_still_get_the_event(monkeypatch):
    bus = EventBus()
    broken = bus.subscribe()
    healthy = bus.subscribe()

    def explode(_event):
        raise RuntimeError("boom")

    monkeypatch.setattr(broken.queue, "put_nowait", explode)
    event = bus.publish("charger.status", "Fleet", "CP1")
    assert event is not None and healthy.queue.qsize() == 1, "no exception reaches the publisher, and the healthy listener has it"
    assert broken.overflowed, "the broken one is cut off instead of being retried on every event"


def test_publishing_never_raises_even_if_the_bus_itself_fails(monkeypatch):
    bus = EventBus()
    monkeypatch.setattr(bus, "_history", None)  # appending to it will fail
    assert bus.publish("charger.status", "Fleet", "CP1") is None


def test_there_is_a_limit_to_how_many_streams_one_broker_serves():
    bus = EventBus(max_listeners=2)
    first = bus.subscribe()
    bus.subscribe()
    with pytest.raises(TooManyListeners):
        bus.subscribe()
    bus.unsubscribe(first)
    bus.subscribe()  # a slot freed up
    assert bus.listener_count == 2


@pytest.mark.asyncio
async def test_closing_the_bus_ends_every_stream_after_what_was_already_queued():
    bus = EventBus()
    waiting = bus.subscribe()  # nothing queued: blocked in next()
    behind = bus.subscribe()
    bus.publish("a", "Fleet", "CP1")
    reader = asyncio.ensure_future(waiting.next(30))
    await asyncio.sleep(0)
    bus.close()
    first = await asyncio.wait_for(reader, 1)
    assert first is not None and first.type == "a", "what was queued is still delivered"
    assert await asyncio.wait_for(waiting.next(30), 1) is None and waiting.finished and not waiting.overflowed
    assert (await behind.next(0.01)).type == "a"
    assert await asyncio.wait_for(behind.next(30), 1) is None, "then the stream is told it is over, without waiting for the keepalive"


@pytest.mark.asyncio
async def test_closing_the_bus_with_a_full_queue_still_ends_the_stream():
    bus = EventBus(queue_size=2)
    listener = bus.subscribe()
    publish_many(bus, 2)  # the queue is full: there is no room for an end marker
    bus.close()
    assert [(await listener.next(0.01)).data["n"] for _ in range(2)] == [0, 1]
    assert await asyncio.wait_for(listener.next(30), 1) is None and listener.finished


def test_a_closed_bus_takes_no_new_listeners_but_keeps_taking_events():
    bus = EventBus()
    bus.close()
    with pytest.raises(BusClosed) as refused:
        bus.subscribe()
    assert isinstance(refused.value, StreamRefused) and "shutting down" in str(refused.value)
    assert bus.publish("a") is not None, "publishing never fails because of shutdown"


def test_replay_after_an_id_returns_what_came_after_it_for_the_filter():
    bus = EventBus()
    bus.publish("a", "Fleet", "CP1")  # 1
    bus.publish("b", "Fleet", "CP2")  # 2
    bus.publish("c", "Fleet", "CP1")  # 3
    replay = bus.replay(1, "Fleet", "CP1")
    assert [e.type for e in replay.events] == ["c"] and replay.missed is False
    assert [e.type for e in bus.replay(3).events] == [] and bus.replay(3).missed is False, "nothing missed when up to date"


def test_replay_says_when_what_was_missed_has_been_forgotten():
    bus = EventBus(history=3)
    publish_many(bus, 6)  # ids 1..6, the last three (4, 5, 6) are remembered
    assert bus.replay(3).missed is False and [e.id for e in bus.replay(3).events] == [4, 5, 6], "3 is the one just before the oldest remembered"
    gap = bus.replay(2)
    assert gap.missed is True and [e.id for e in gap.events] == [4, 5, 6], "event 3 is gone; the client is told so"
    assert bus.replay(0).missed is True


def test_replay_of_an_id_from_a_later_run_is_reported_as_missed():
    bus = EventBus()
    publish_many(bus, 2)
    assert bus.replay(40).missed is True and bus.replay(40).events == []


def test_the_first_connection_can_ask_for_the_latest_matching_events():
    bus = EventBus()
    bus.publish("a", "Fleet", "CP1")
    bus.publish("b", "Home", "W1")
    bus.publish("c", "Fleet", "CP1")
    bus.publish("d", "Fleet", "CP1")
    assert [e.type for e in bus.replay(None, "Fleet", None, latest=2).events] == ["c", "d"]
    assert bus.replay(None, latest=0).events == [], "no replay unless asked"
    assert bus.replay(None, "Home", latest=5).events[0].type == "b"


def test_the_history_is_bounded():
    bus = EventBus(history=10)
    publish_many(bus, 100)
    assert len(bus.replay(0).events) == 10


# ---------------------------------------------------------------------------
# SSE text
# ---------------------------------------------------------------------------
def parse(chunk):
    fields = {}
    for line in chunk.rstrip("\n").split("\n"):
        name, _, value = line.partition(": ")
        fields[name] = value
    return fields


def test_an_event_is_one_sse_message_with_its_id_and_name():
    bus = EventBus()
    event = bus.publish("charger.status", "Fleet", "CP1", connector_id=1, status="Charging")
    text = sse_message(event)
    assert text.endswith("\n\n") and text.count("\n\n") == 1
    fields = parse(text)
    assert fields["id"] == "1" and fields["event"] == "charger.status"
    body = json.loads(fields["data"])
    assert body == {
        "id": 1, "type": "charger.status", "time": event.time.isoformat(), "org": "Fleet", "charger_id": "CP1",
        "data": {"connector_id": 1, "status": "Charging"},
    }


def test_text_with_newlines_cannot_break_the_message_framing():
    bus = EventBus()
    event = bus.publish("command.result", "Fleet", "CP1", error="line one\nline two\r\n\r\ndata: injected")
    text = sse_message(event)
    assert text.count("\n\n") == 1, "json escapes newlines, so one message stays one message"
    assert json.loads(parse(text)["data"])["data"]["error"].endswith("data: injected")


async def collect(stream, count, timeout=2.0):
    chunks = []

    async def read():
        async for chunk in stream:
            chunks.append(chunk)
            if len(chunks) >= count:
                return

    await asyncio.wait_for(read(), timeout)
    return chunks


@pytest.mark.asyncio
async def test_a_stream_opens_with_the_run_id_then_replays_then_goes_live():
    bus = EventBus()
    publish_many(bus, 3)  # 1, 2, 3
    stream = event_stream(bus, "run-1", after_id=1)
    opening, *replayed = await collect(stream, 3)
    assert parse(opening)["event"] == "stream.open"
    assert json.loads(parse(opening)["data"]) == {"instance_id": "run-1", "last_id": 3, "missed": False}
    assert [parse(c)["id"] for c in replayed] == ["2", "3"]
    live = asyncio.ensure_future(collect(stream, 1))
    await asyncio.sleep(0.01)
    bus.publish("charger.status", "Fleet", "CP1")
    assert parse((await live)[0])["id"] == "4"
    await stream.aclose()
    assert bus.listener_count == 0, "closing the stream gives its slot back"


@pytest.mark.asyncio
async def test_an_event_published_while_replaying_is_sent_once(monkeypatch):
    """The stream subscribes before it reads the history; an event landing in between is in both, and must be sent once."""
    bus = EventBus()
    publish_many(bus, 2)
    real_replay = bus.replay

    def replay_with_a_race(*args, **kwargs):
        bus.publish("charger.status", "Fleet", "CP1")  # 3: reaches the listener's queue and the history before the history is read
        return real_replay(*args, **kwargs)

    monkeypatch.setattr(bus, "replay", replay_with_a_race)
    stream = event_stream(bus, "run-1", after_id=0, keepalive=0.05)
    chunks = await collect(stream, 5)
    assert [parse(c).get("id") for c in chunks[1:4]] == ["1", "2", "3"]
    assert chunks[4] == ": keepalive\n\n", "and nothing else follows: 3 was not sent a second time"
    await stream.aclose()


@pytest.mark.asyncio
async def test_a_stream_says_when_the_client_missed_events():
    bus = EventBus(history=2)
    publish_many(bus, 5)
    opening = await event_stream(bus, "run-1", after_id=1).__anext__()
    assert json.loads(parse(opening)["data"])["missed"] is True


@pytest.mark.asyncio
async def test_a_first_connection_starts_with_the_requested_number_of_recent_events_only():
    bus = EventBus()
    publish_many(bus, 5)
    stream = event_stream(bus, "run-1", replay=2)
    chunks = await collect(stream, 3)
    assert [parse(c)["id"] for c in chunks[1:]] == ["4", "5"]
    await stream.aclose()
    # asking for more than the cap is clamped, not honoured
    bus = EventBus(history=MAX_REPLAY * 3)
    publish_many(bus, MAX_REPLAY * 2)
    stream = event_stream(bus, "run-1", replay=MAX_REPLAY * 2, keepalive=0.05)
    chunks = await collect(stream, MAX_REPLAY + 2)
    assert chunks[-1] == ": keepalive\n\n" and len(chunks) == MAX_REPLAY + 2, "the opening message, MAX_REPLAY events, then silence"
    await stream.aclose()


@pytest.mark.asyncio
async def test_an_idle_stream_sends_keepalive_comments():
    bus = EventBus()
    stream = event_stream(bus, "run-1", keepalive=0.02)
    chunks = await collect(stream, 3)
    assert chunks[1:] == [": keepalive\n\n", ": keepalive\n\n"]
    await stream.aclose()


@pytest.mark.asyncio
async def test_a_stream_ends_when_the_bus_is_closed():
    bus = EventBus()
    stream = event_stream(bus, "run-1", keepalive=30)
    await stream.__anext__()
    pending = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0.01)
    bus.close()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, 1)
    assert bus.listener_count == 0


@pytest.mark.asyncio
async def test_a_stream_ends_when_its_listener_falls_behind():
    bus = EventBus(queue_size=2)
    stream = event_stream(bus, "run-1", keepalive=5)
    await stream.__anext__()
    publish_many(bus, 10)  # nobody is reading
    got = [c async for c in stream]
    assert [parse(c)["id"] for c in got] == ["1", "2"], "what was queued, then the stream ends so the client reconnects"
    assert bus.listener_count == 0
