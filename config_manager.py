"""Data Layer / Storage — хранение config.json + управление .cache_processing/."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

APP_NAME = "PixelForge"
CONFIG_FILENAME = "config.json"
CACHE_DIRNAME = ".cache_processing"

DEFAULTS: dict = {
    "output_format": "WebP",        # WebP | JPEG
    "quality": 80,                  # 10..100
    "interpolation": "Lanczos",      # Lanczos | Bicubic | Bilinear
    "output_dir": "",
    "resize_enabled": False,
    "width": 1920,
    "height": 1080,
    "keep_aspect": True,
}


def get_config_path() -> Path:
    """AppData (Windows) либо рабочая директория — по ТЗ п.1.5."""
    appdata = os.environ.get("APPDATA")
    if os.name == "nt" and appdata:
        d = Path(appdata) / APP_NAME
    else:
        d = Path.cwd()
    d.mkdir(parents=True, exist_ok=True)
    return d / CONFIG_FILENAME


def get_cache_dir() -> Path:
    """Скрытая системная подпапка .cache_processing/ — по ТЗ п.1.5."""
    # Временный каталог внутри системного temp, чтобы не засорять проект.
    base = Path(tempfile.gettempdir()) / APP_NAME / CACHE_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    return base


class ConfigManager:
    def __init__(self, path: Path | None = None):
        self.path = path or get_config_path()
        self.data: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> dict:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k, v in DEFAULTS.items():
                    if k in raw:
                        self.data[k] = raw[k]
        except (json.JSONDecodeError, OSError):
            self.data = dict(DEFAULTS)
        # Нормализация
        try:
            self.data["quality"] = max(10, min(100, int(self.data.get("quality", 80))))
        except (TypeError, ValueError):
            self.data["quality"] = 80
        if self.data.get("output_format") not in ("WebP", "JPEG"):
            self.data["output_format"] = "WebP"
        if self.data.get("interpolation") not in ("Lanczos", "Bicubic", "Bilinear"):
            self.data["interpolation"] = "Lanczos"
        return self.data

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def get(self, key: str, default=None):
        return self.data.get(key, default if default is not None else DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def update(self, mapping: dict) -> None:
        self.data.update(mapping)
        self.save()

    @staticmethod
    def clear_cache(cache_dir: Path | None = None) -> None:
        """Безвозвратное удаление файлов кэша методами ОС — по ТЗ п.1.5."""
        d = cache_dir or get_cache_dir()
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
