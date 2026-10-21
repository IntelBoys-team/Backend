"""Серверное состояние и конвейер обработки.

Модуль принадлежит серверу: не знает про окно/UI и не запускает клиент.
UI получает состояние только через /api/* и SSE.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from archive_validator import (
    SUPPORTED_EXTS,
    ValidationError,
    collect_from_folder,
    is_archive,
    validate_and_extract,
    validate_single_image,
)
from config_manager import APP_NAME, ConfigManager, get_cache_dir
from thread_manager import ThreadManager

from .events import EventBus

STATUS_IDLE = "idle"
STATUS_VALIDATING = "validating"
STATUS_READY = "ready"
STATUS_CONVERTING = "converting"
STATUS_DONE = "done"
STATUS_ABORTED = "aborted"
STATUS_ERROR = "error"


@dataclass
class Source:
    files: list[Path] = field(default_factory=list)
    label: str = ""
    name: str = ""
    kind: str = "none"
    from_cache: bool = False
    base_dir: Path | None = None
    upload_path: Path | None = None

    @property
    def ready(self) -> bool:
        return bool(self.files)

    def snapshot(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "label": self.label,
            "name": self.name,
            "kind": self.kind,
            "count": len(self.files),
            "from_cache": self.from_cache,
        }


@dataclass
class Job:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = STATUS_IDLE
    done: int = 0
    total: int = 0
    errors: int = 0
    output_dir: str = ""
    output_files: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None


class AppState:
    """Single shared pipeline state for the desktop window and the web client."""

    def __init__(self) -> None:
        self.cfg = ConfigManager()
        self.bus = EventBus()
        self.cache_dir = get_cache_dir()
        runtime = Path(tempfile.gettempdir()) / APP_NAME
        self.runtime_dir = runtime
        self.uploads_dir = runtime / "uploads"
        self.outputs_dir = runtime / "outputs"
        self.downloads_dir = runtime / "downloads"
        for d in (self.uploads_dir, self.outputs_dir, self.downloads_dir):
            d.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self.source = Source()
        self.job = Job()
        self._tm: ThreadManager | None = None
        self._running = False

    # ------------------------------------------------------------------ events
    def log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        with self._lock:
            self.job.logs.append(line)
            if len(self.job.logs) > 4000:
                del self.job.logs[: len(self.job.logs) - 4000]
        self.bus.publish({"type": "log", "message": line})

    def _job_snapshot(self) -> dict[str, Any]:
        with self._lock:
            files = []
            for raw in self.job.output_files:
                p = Path(raw)
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                files.append({"name": p.name, "size": size})
            return {
                "id": self.job.id,
                "status": self.job.status,
                "done": self.job.done,
                "total": self.job.total,
                "errors": self.job.errors,
                "output_dir": self.job.output_dir,
                "files": files,
                "can_download": bool(files),
                "started_at": self.job.started_at,
                "finished_at": self.job.finished_at,
                "logs": list(self.job.logs[-400:]),
            }

    def job_snapshot(self) -> dict[str, Any]:
        return self._job_snapshot()

    def source_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self.source.snapshot()

    def _publish_state(self) -> None:
        self.bus.publish({"type": "state", "job": self._job_snapshot()})

    def _publish_source(self) -> None:
        self.bus.publish({"type": "source", "source": self.source_snapshot()})

    def _set_status(self, status: str) -> None:
        with self._lock:
            self.job.status = status
        self._publish_state()

    # ------------------------------------------------------------------ config
    def config_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.cfg.data)

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = set(self.cfg.data.keys())
        clean = {k: v for k, v in values.items() if k in allowed and v is not None}
        with self._lock:
            self.cfg.update(clean)
            return dict(self.cfg.data)

    # ------------------------------------------------------------------ source
    def prepare_source(
        self,
        raw_path: str | Path,
        display_name: str | None = None,
        is_upload: bool = False,
        validate_images: bool = False,
    ) -> dict[str, Any]:
        """Подготовка источника.

        is_upload=True — файл/папка лежит во временном каталоге загрузок и будет
        удалён после цикла. validate_images=True включает Magic Numbers для
        пакетной загрузки папки (Drag-and-Drop без архива).
        """
        path = Path(raw_path)
        with self._lock:
            if self._running:
                raise RuntimeError("Конвертация уже выполняется")
            self._reset_source_locked()
            self.job = Job(status=STATUS_VALIDATING)
        self._publish_source()
        self._publish_state()

        name = display_name or path.name
        self.log(f"Загрузка источника: {name}")

        try:
            if is_archive(path):
                self.log("Запуск сквозной валидации... проверка сигнатур")
                files = validate_and_extract(path, self.cache_dir)
                kind = "archive"
                base_dir = None if is_upload else path.parent
                from_cache = True
                self.log(f"Валидация успешна. Найдено {len(files)} изображений")
            elif path.is_dir():
                files = collect_from_folder(path)
                if not files:
                    raise ValidationError("В папке нет изображений PNG/JPEG/BMP")
                if validate_images:
                    # Для Drag-and-Drop папки проверяем не только расширение,
                    # но и сигнатуру каждого файла (ТЗ п.2.3).
                    files = [validate_single_image(p) for p in files]
                kind = "upload" if is_upload else "folder"
                base_dir = None if is_upload else path
                from_cache = is_upload
                self.log(f"Валидация успешна. Найдено {len(files)} изображений в папке")
            elif path.is_file():
                if path.suffix.lower() not in SUPPORTED_EXTS:
                    raise ValidationError(
                        "Ошибка валидации: архив содержит неподдерживаемые типы данных. "
                        "Конвертация невозможна"
                    )
                files = [validate_single_image(path)]
                kind = "upload" if is_upload else "file"
                base_dir = None if is_upload else path.parent
                from_cache = is_upload
                self.log("Валидация успешна. Найдено 1 изображение")
            else:
                raise ValidationError(f"Путь не найден: {path}")

            with self._lock:
                upload = path if is_upload else None
                self.source = Source(
                    files=files,
                    label=f"{name} — {len(files)} файл(ов) готово к конвертации",
                    name=name,
                    kind=kind,
                    from_cache=from_cache,
                    base_dir=base_dir,
                    upload_path=upload,
                )
                self.job.status = STATUS_READY
                self.job.total = len(files)
            self._publish_source()
            self._publish_state()
            return self.source_snapshot()
        except Exception as exc:
            if is_upload:
                try:
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                self._cleanup_upload_locked()
            ConfigManager.clear_cache(self.cache_dir)
            with self._lock:
                logs = self.job.logs
                self.source = Source()
                self.job = Job(status=STATUS_ERROR, logs=logs)
            self.log(f"Ошибка: {exc}. Процесс остановлен.")
            self._publish_source()
            self._publish_state()
            raise

    def _reset_source_locked(self) -> None:
        self._cleanup_upload_locked()
        ConfigManager.clear_cache(self.cache_dir)
        self.source = Source()

    def _cleanup_upload_locked(self) -> None:
        """Удаляет временные файлы загрузки: одиночный файл или папку (batch)."""
        if self.source.upload_path is not None:
            try:
                if self.source.upload_path.is_dir():
                    shutil.rmtree(self.source.upload_path, ignore_errors=True)
                else:
                    self.source.upload_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.source.upload_path = None

    # ------------------------------------------------------------------ convert
    def start_conversion(self, settings: Any) -> dict[str, Any]:
        """Создаёт задачу и уходит в фоновый поток.

        HTTP-запрос не ждёт конвертацию: клиент получает снимок job сразу,
        дальнейшие изменения приходят через SSE.
        """
        with self._lock:
            if self._running:
                raise RuntimeError("Конвертация уже выполняется")
            if not self.source.ready:
                raise ValueError("Источник не выбран или не прошёл валидацию")

            files = list(self.source.files)
            self._running = True
            self.job = Job(
                status=STATUS_CONVERTING,
                total=len(files),
                started_at=time.time(),
            )
            output_dir = self._resolve_output_dir_locked(settings.output_dir)
            self.job.output_dir = str(output_dir)
            self.cfg.update({
                "output_format": settings.output_format,
                "quality": settings.quality,
                "interpolation": settings.interpolation,
                "resize_enabled": settings.resize_enabled,
                "width": settings.width or 1920,
                "height": settings.height or 1080,
                "keep_aspect": settings.keep_aspect,
                "output_dir": settings.output_dir or "",
            })

        kwargs = {
            "output_format": settings.output_format,
            "quality": settings.quality,
            "interpolation": settings.interpolation,
            "resize_enabled": settings.resize_enabled,
            "width": settings.width if settings.resize_enabled else None,
            "height": settings.height if settings.resize_enabled else None,
            "keep_aspect": settings.keep_aspect,
        }
        self.log(
            f"Старт: формат={kwargs['output_format']} качество={kwargs['quality']}% "
            f"интерполяция={kwargs['interpolation']} файлов={len(files)}"
        )
        self._publish_state()

        threading.Thread(
            target=self._run_conversion,
            args=(files, output_dir, kwargs),
            daemon=True,
            name="pixelforge-convert",
        ).start()
        return self.job_snapshot()

    def _resolve_output_dir_locked(self, requested: str | None) -> Path:
        if requested and requested.strip():
            return Path(requested.strip()).expanduser()
        if self.source.base_dir is not None:
            return self.source.base_dir / "Converted"
        return self.outputs_dir / self.job.id

    def _run_conversion(self, files: list[Path], output_dir: Path, kwargs: dict[str, Any]) -> None:
        # Пул/очередь/Mutex находятся в ThreadManager — UI их не касается.
        tm = ThreadManager()
        with self._lock:
            self._tm = tm
        finished_status = STATUS_DONE
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            tm.start(
                files,
                output_dir,
                kwargs,
                on_progress=self._on_progress,
                on_ok=self._on_ok,
                on_error=self._on_error,
            )
            if tm.aborted:
                finished_status = STATUS_ABORTED
                self.log("Прервано пользователем. Очередь очищена.")
            else:
                self.log(f"Готово. Результаты: {output_dir}")
        except Exception as exc:  # noqa: BLE001
            finished_status = STATUS_ERROR
            self.log(f"Критическая ошибка конвертации: {exc}")
        finally:
            from_cache = False
            with self._lock:
                from_cache = self.source.from_cache
                self._tm = None
            if from_cache:
                self._clear_source_after_job()
            else:
                ConfigManager.clear_cache(self.cache_dir)
            with self._lock:
                self._running = False
                self.job.status = finished_status
                self.job.finished_at = time.time()
            self._publish_source()
            self._publish_state()

    def _on_progress(self, done: int, total: int) -> None:
        with self._lock:
            self.job.done = done
            self.job.total = total
        self._publish_state()

    def _on_ok(self, src: Path, dst: Path) -> None:
        with self._lock:
            self.job.output_files.append(str(dst))
        self.log(f"OK  {src.name} -> {dst.name}")

    def _on_error(self, src: Path, error: str) -> None:
        with self._lock:
            self.job.errors += 1
        self.log(f"ОШИБКА {src.name}: {error}")

    def _clear_source_after_job(self) -> None:
        ConfigManager.clear_cache(self.cache_dir)
        with self._lock:
            self._cleanup_upload_locked()
            self.source = Source()
        self.log("Кэш .cache_processing очищен. Для следующего цикла выберите источник заново.")

    def abort(self) -> dict[str, Any]:
        with self._lock:
            tm = self._tm
            running = self._running
        if running and tm is not None:
            tm.abort()
            self.log("Abort: остановка пула, очистка очереди и кэша…")
        return self.job_snapshot()

    # ------------------------------------------------------------------ output
    def build_zip(self) -> Path:
        """ZIP нужен веб-клиенту; desktop берёт файлы прямо из output_dir."""
        with self._lock:
            files = [Path(p) for p in self.job.output_files]
            job_id = self.job.id
        existing = [p for p in files if p.is_file()]
        if not existing:
            raise ValueError("Нет результатов для скачивания")
        zip_path = self.downloads_dir / f"PixelForge-{job_id}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            used: set[str] = set()
            for p in existing:
                arc = p.name
                stem, suffix = p.stem, p.suffix
                i = 1
                while arc in used:
                    arc = f"{stem}_{i}{suffix}"
                    i += 1
                used.add(arc)
                zf.write(p, arcname=arc)
        return zip_path

    def open_output(self) -> str:
        with self._lock:
            raw = self.job.output_dir
        if not raw:
            raise ValueError("Папка результатов ещё не создана")
        path = Path(raw)
        if not path.exists():
            raise ValueError("Папка результатов не существует")
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return str(path)

    # ------------------------------------------------------------------ lifecycle
    def shutdown(self) -> None:
        with self._lock:
            tm = self._tm
        if tm is not None:
            tm.abort()
        ConfigManager.clear_cache(self.cache_dir)
