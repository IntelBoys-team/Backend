"""Точка входа desktop-клиента PixelForge.

Сервер и клиент разделены:
    python server.py   # API (запускается отдельно)
    python main.py     # лёгкий desktop UI, работает по API

Клиент не стартует сервер: если API недоступен, UI покажет это в статусе.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    parser = argparse.ArgumentParser(description="PixelForge desktop client")
    parser.add_argument(
        "--api",
        default=None,
        help="Адрес API, например http://127.0.0.1:8000 (или env PIXELFORGE_API)",
    )
    args = parser.parse_args()

    try:
        from desktop import run
        return run(args.api)
    except ImportError as exc:
        print(exc)
        print("Выполните: python -m pip install -r requirements.txt")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
