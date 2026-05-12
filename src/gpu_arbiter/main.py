from __future__ import annotations

import argparse
import logging

import uvicorn

from gpu_arbiter.app import create_app
from gpu_arbiter.config import load_config
from gpu_arbiter.queue.sqlite_store import SQLiteTaskStore
from gpu_arbiter.vram import NVMLVRAMProbe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--db", default="./queue.db", help="Path to SQLite queue database")
    return parser


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("gpu_arbiter")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False


def main() -> None:
    _configure_logging()
    args = build_parser().parse_args()
    config = load_config(args.config)
    app = create_app(
        config,
        vram_probe=NVMLVRAMProbe(config.gpu.index),
        task_store=SQLiteTaskStore(args.db),
    )
    uvicorn.run(app, host=args.host, port=args.port)
