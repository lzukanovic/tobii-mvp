"""
Async bridge for running g3pylib coroutines from synchronous Flask handlers.

g3pylib is fully async (asyncio). Flask runs synchronously with threading.
This module provides a dedicated asyncio event loop running in a background
daemon thread, with helpers to submit async work from Flask handlers.
"""
import asyncio
import threading


_loop = None
_thread = None


def start_async_loop():
    """Start the background asyncio event loop. Call once at app startup."""
    global _loop, _thread

    _loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(_loop)
        _loop.run_forever()

    _thread = threading.Thread(target=_run, daemon=True)
    _thread.start()


def get_loop():
    """Get the background asyncio event loop."""
    if _loop is None:
        raise RuntimeError("Async loop not started. Call start_async_loop() first.")
    return _loop


def run_coroutine_sync(coro, timeout=30):
    """
    Submit an async coroutine to the background loop and block until it completes.

    Args:
        coro: Coroutine to run
        timeout: Max seconds to wait for result

    Returns:
        The coroutine's return value

    Raises:
        Exception: Any exception raised by the coroutine
    """
    future = asyncio.run_coroutine_threadsafe(coro, get_loop())
    return future.result(timeout=timeout)


def run_coroutine(coro):
    """
    Submit an async coroutine to the background loop (fire-and-forget).

    Use this for long-running tasks like stream receiver loops.

    Returns:
        asyncio.Task — cancel it with cancel_task() to properly stop the loop.
    """
    import concurrent.futures as cf
    loop = get_loop()
    task_future = cf.Future()

    def _create():
        task = loop.create_task(coro)
        task_future.set_result(task)

    loop.call_soon_threadsafe(_create)
    return task_future.result(timeout=5)


def cancel_task(task):
    """
    Cancel an asyncio Task from a synchronous (non-loop) thread.

    asyncio.Task.cancel() is not thread-safe; this schedules the
    cancellation on the event loop's thread instead.
    """
    if task is not None:
        get_loop().call_soon_threadsafe(task.cancel)
