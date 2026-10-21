"""
    python server.py                 # http://127.0.0.1:8000
    python server.py --host 0.0.0.0  # доступ по сети (API готов для веба)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from api.app import create_app


def run(host: str = "127.0.0.1", port: int = 8000) -> int:
    print(f"PixelForge API: http://{host}:{port}")
    print(f"Swagger:        http://{host}:{port}/docs")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PixelForge API server")
    parser.add_argument("--host", default="127.0.0.1", help="Адрес прослушивания")
    parser.add_argument("--port", type=int, default=8000, help="Порт")
    args = parser.parse_args()
    return run(args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
