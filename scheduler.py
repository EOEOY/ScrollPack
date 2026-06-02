import asyncio
from collections import deque
import time

class SchedulerController:
    def __init__(self):
        self._pause = False
    
    def pause(self):
        self._pause = True
    
    def resume(self):
        self._pause = False

class Scheduler:
    def __init__(self, n: int, per_seconds: float):
        if n > 0 and per_seconds > 0:
            self._gap = per_seconds / n
        else:
            self._gap = 0
        self._queue = deque()
        self._controller = SchedulerController()
        self._running = False
        self._lock = asyncio.Lock()
        self._last_run = 0.0
    
    async def run(self, task):
        fut = asyncio.Future()
        self._queue.append((task, fut))
        if not self._running:
            asyncio.ensure_future(self._loop())
        return await fut
    
    async def _loop(self):
        if self._running:
            return
        self._running = True
        try:
            while self._queue:
                while self._controller._pause:
                    await asyncio.sleep(0.1)
                
                elapsed = time.monotonic() - self._last_run
                if elapsed < self._gap:
                    await asyncio.sleep(self._gap - elapsed)
                
                task_fn, fut = self._queue.popleft()
                try:
                    result = task_fn(self._controller)
                    if asyncio.iscoroutine(result):
                        result = await result
                    fut.set_result(result)
                except Exception as e:
                    fut.set_exception(e)
                
                self._last_run = time.monotonic()
        finally:
            self._running = False
