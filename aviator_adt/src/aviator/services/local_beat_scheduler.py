"""Custom Celery beat scheduler with optional in-process task execution.

Entries marked with ``"options": {"local": True}`` are executed in the beat
process via a dedicated background event loop thread, so scheduling remains
non-blocking.

If ``"local"`` is omitted or false, default Celery behavior is used and the
task is published to the broker.

The ``"task"`` value must match a registered Celery task name.

Usage in celeryconfig.py:
    beat_schedule = {
        "my-local-task": {
            "task": "aviator.tasks.beat_tasks.cleanup_usage_transactions",
            "schedule": crontab(hour=2, minute=0),
            "options": {"local": True},
        },
    }

Start beat with the custom scheduler::

    celery -A aviator.celery beat -S aviator.services.local_beat_scheduler.LocalBeatScheduler ...
"""

import asyncio
import inspect
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from celery.beat import PersistentScheduler, ScheduleEntry
from kombu import Producer

logger = logging.getLogger(__name__)

_background_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def _start_background_loop() -> asyncio.AbstractEventLoop:
    """Start a persistent event loop in a daemon thread."""
    global _background_loop, _loop_thread, _executor  # noqa: PLW0603

    with _lock:
        if _background_loop and _background_loop.is_running():
            return _background_loop

        _background_loop = asyncio.new_event_loop()
        _executor = ThreadPoolExecutor(thread_name_prefix="local-scheduler-worker")
        _background_loop.set_default_executor(_executor)

        def run_loop(loop: asyncio.AbstractEventLoop) -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        _loop_thread = threading.Thread(
            target=run_loop,
            args=(_background_loop,),
            daemon=True,
            name="local-scheduler-loop",
        )

        _loop_thread.start()

        return _background_loop


def _stop_background_loop() -> None:
    """Stop and close the background event loop if it is running."""
    global _background_loop, _loop_thread, _executor  # noqa: PLW0603

    with _lock:
        loop = _background_loop
        thread = _loop_thread
        executor = _executor
        _background_loop = None
        _loop_thread = None
        _executor = None

    if loop is None:
        return

    try:
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

        if thread and thread.is_alive():
            thread.join(timeout=2)
    except Exception:
        logger.exception("LocalBeatScheduler: failed while stopping background loop")
    finally:
        if not loop.is_closed():
            loop.close()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


class LocalBeatScheduler(PersistentScheduler):
    """Beat scheduler that executes local tasks in a background event loop.

    All local tasks — async or sync — are submitted to a single persistent
    ``asyncio`` event loop running in a daemon thread.  ``apply_entry``
    returns immediately so the beat main thread is never blocked.

    * **async tasks** run directly in the event loop.
        * **sync tasks** are executed via ``loop.run_in_executor`` using a
            persistent thread pool.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Initialize scheduler and start a dedicated background event loop."""
        super().__init__(*args, **kwargs)
        self._loop = _start_background_loop()
        self._executor = _executor
        if self._executor is None:
            raise RuntimeError

    def apply_entry(self, entry: ScheduleEntry, producer: Producer | None = None) -> None:
        """Run an entry locally when configured, otherwise delegate to Celery beat."""
        if not entry.options.get("local", False):
            super().apply_entry(entry, producer=producer)
            return

        task = self.app.tasks.get(entry.task)
        if task is None:
            logger.error("LocalBeatScheduler: task %r not found in registry", entry.task)
            return

        async def _execute() -> None:
            try:
                if inspect.iscoroutinefunction(task.run):
                    result = await task.run(*entry.args, **entry.kwargs)
                else:
                    result = await self._loop.run_in_executor(self._executor, task.apply, entry.args, entry.kwargs)
                logger.info("LocalBeatScheduler: task %s completed - %s", entry.name, result)
            except Exception:
                logger.exception("LocalBeatScheduler: task %s failed", entry.name)

        asyncio.run_coroutine_threadsafe(_execute(), self._loop)
        logger.info("LocalBeatScheduler: task %s submitted to event loop", entry.name)

    def close(self) -> None:
        """Close scheduler resources, including PG pool shutdown and loop teardown."""
        try:
            try:
                from aviator.database.pg_client import PgConnectionPool

                if self._loop.is_running() and not self._loop.is_closed():
                    close_coroutine = PgConnectionPool.close_pool()
                    try:
                        future = asyncio.run_coroutine_threadsafe(close_coroutine, self._loop)
                        future.result(timeout=5)
                    except RuntimeError:
                        # If the loop closed between the guard and submit, avoid leaking coroutine.
                        close_coroutine.close()
                        asyncio.run(PgConnectionPool.close_pool())
                else:
                    asyncio.run(PgConnectionPool.close_pool())
            except Exception:
                logger.exception("LocalBeatScheduler: failed to close async postgres pool")

            super().close()
        finally:
            _stop_background_loop()
