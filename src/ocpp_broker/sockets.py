"""
Serialised, bounded socket writes.

Several tasks can write to the same socket (the OCPP library replying to calls,
REST-issued commands, the relay forwarding backend traffic). Every send goes
through ``locked_send`` with that socket's lock so frames are never interleaved,
and with a timeout so a wedged peer cannot hold the lock forever.
"""

import asyncio
from typing import Awaitable, Callable, Optional

# Seconds a single frame may take to hand to the transport before we give up.
SEND_TIMEOUT = 10.0


async def locked_send(
    lock: asyncio.Lock,
    send: Callable[[str], Awaitable[None]],
    message: str,
    timeout: Optional[float] = None,
) -> None:
    """Send ``message`` while holding ``lock``; raise asyncio.TimeoutError if stuck."""
    async with lock:
        await asyncio.wait_for(send(message), SEND_TIMEOUT if timeout is None else timeout)
