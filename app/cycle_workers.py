"""Keep synchronous worker results alive across asyncio timeouts (one event loop)."""
import asyncio
from typing import Callable, Any


class CycleWorkers:
    def __init__(self):
        self._pending: dict[int, asyncio.Future] = {}

    async def run(self, user_id: int, compute: Callable[[int], Any], timeout: float):
        future = self._pending.get(user_id)
        if future is None:
            future = asyncio.get_running_loop().run_in_executor(None, compute, user_id)
            self._pending[user_id] = future
        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # A TimeoutError raised by compute is a completed failure, not a waiter timeout.
            if future.done() and not future.cancelled() and future.exception() is not None:
                self._pending.pop(user_id, None)
            # Cancelling the waiter cannot stop the thread. Keep it for the next cycle.
            raise
        except BaseException:
            if future.done():
                self._pending.pop(user_id, None)
            raise
        self._pending.pop(user_id, None)
        return result
