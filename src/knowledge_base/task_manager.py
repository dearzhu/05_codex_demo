"""Background task manager with concurrency control"""

import asyncio
import logging
import time
from typing import Callable, Coroutine, Any, Optional

logger = logging.getLogger(__name__)


class Task:
    """A unit of work processed by the task manager"""

    def __init__(self, name: str, coro_factory: Callable[[], Coroutine],
                 retry_count: int = 0, max_retries: int = 3):
        self.name = name
        self.coro_factory = coro_factory
        self.retry_count = retry_count
        self.max_retries = max_retries
        self.backoff = 1.0  # seconds

    async def execute(self) -> Any:
        return await self.coro_factory()


class TaskManager:
    """Lightweight async task queue with retry and backoff"""

    def __init__(self, max_workers: int = 2, max_retries: int = 3):
        self.queue: asyncio.Queue[Task] = asyncio.Queue()
        self.max_retries = max_retries
        self.max_workers = max_workers
        self._workers: list[asyncio.Task] = []
        self._running = False

    async def start(self):
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(self.max_workers)
        ]
        logger.info(f"TaskManager started with {self.max_workers} workers")

    async def stop(self, wait: bool = True, timeout: float = 30.0):
        self._running = False
        if wait:
            remaining = timeout
            while not self.queue.empty() and remaining > 0:
                await asyncio.sleep(0.5)
                remaining -= 0.5
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        logger.info("TaskManager stopped")

    async def enqueue(self, task: Task):
        await self.queue.put(task)
        logger.debug(f"Task enqueued: {task.name}")

    async def _worker(self, wid: int):
        while self._running:
            try:
                task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                logger.info(f"[W{ wid}] processing: {task.name}")
                await task.execute()
                logger.info(f"[W{ wid}] completed: {task.name}")
            except Exception as e:
                if task.retry_count < self.max_retries:
                    task.retry_count += 1
                    task.backoff = min(60.0, task.backoff * 2)
                    logger.warning(
                        f"[W{ wid}] {task.name} failed (retry {task.retry_count}/{self.max_retries}): {e}"
                    )
                    await asyncio.sleep(task.backoff)
                    await self.queue.put(task)
                else:
                    logger.error(f"[W{ wid}] {task.name} failed after {self.max_retries} retries: {e}")
            finally:
                self.queue.task_done()


# ── Semaphore group for pipeline stages ──

class PipelineSemaphores:
    """Shared semaphores controlling concurrency across pipeline stages"""

    def __init__(self, max_parse: int = 4, max_ocr: int = 2, max_embed_batch: int = 32):
        self.parse = asyncio.Semaphore(max_parse)
        self.ocr = asyncio.Semaphore(max_ocr)
        self.embed_batch_size = max_embed_batch
        self.embed_queue: asyncio.Queue = asyncio.Queue()

    def acquire_parse(self) -> asyncio.Semaphore:
        return self.parse

    def acquire_ocr(self) -> asyncio.Semaphore:
        return self.ocr
