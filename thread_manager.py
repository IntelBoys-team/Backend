"""Service Layer — Thread Manager: пул потоков + FIFO очередь + Mutex-счётчик."""
from __future__ import annotations

import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from image_core import convert_image


class ThreadManager:
    """Пул рабочих потоков — ТЗ п.2.2.

    - workers = число логических ядер (мин. 2, макс. 16)
    - задачи лежат в потокобезопасной FIFO-очереди
    - общий счётчик counter++ защищён threading.Lock (Mutex)
    """

    def __init__(self, max_workers: int | None = None):
        cpu = os.cpu_count() or 4
        self.max_workers = max_workers or max(2, min(16, cpu))
        self._queue: queue.Queue[Path] = queue.Queue()
        self._lock = threading.Lock()  # Mutex счётчика
        self._abort = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        self._done = 0
        self._total = 0

    @property
    def aborted(self) -> bool:
        return self._abort.is_set()

    def pending_count(self) -> int:
        return self._queue.qsize()

    def abort(self) -> None:
        """Экстренная очистка очереди — кнопка «Прервать» (ТЗ п.3)."""
        self._abort.set()
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def reset(self) -> None:
        self._abort.clear()
        self._done = 0
        self._total = 0
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break

    def start(
        self,
        files: list[Path],
        dst_dir: Path,
        convert_kwargs: dict,
        on_progress: Callable[[int, int], None] | None = None,
        on_ok: Callable[[Path, Path], None] | None = None,
        on_error: Callable[[Path, str], None] | None = None,
        on_finished: Callable[[int, int], None] | None = None,
    ) -> None:
        """Запуск пула. Неблокирующий возврат невозможен — вызывайте из фонового потока UI."""
        self.reset()
        for f in files:
            self._queue.put(f)
        self._total = len(files)

        def worker() -> None:
            while not self._abort.is_set():
                try:
                    src = self._queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    if self._abort.is_set():
                        self._queue.task_done()
                        return
                    try:
                        dst = convert_image(src, dst_dir, **convert_kwargs)
                        if on_ok:
                            on_ok(src, dst)
                    except Exception as e:  # битый файл — логируем, идём дальше (ТЗ п.3.4)
                        if on_error:
                            on_error(src, str(e))
                finally:
                    # --- Mutex: захват блокировки на counter++ (ТЗ п.2.2) ---
                    with self._lock:
                        self._done += 1
                        done, total = self._done, self._total
                    # --- Unlock ---
                    self._queue.task_done()
                    if on_progress:
                        on_progress(done, total)

        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        futures = [self._executor.submit(worker) for _ in range(min(self.max_workers, max(1, self._total)))]
        for fu in futures:
            try:
                fu.result()
            except Exception:
                pass
        self._executor.shutdown(wait=True)
        if on_finished and not self._abort.is_set():
            on_finished(self._done, self._total)
