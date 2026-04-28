from __future__ import annotations

import argparse

import uvicorn

from gpu_arbiter.app import create_app
from gpu_arbiter.config import load_config
from gpu_arbiter.vram import NVMLVRAMProbe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    app = create_app(config, vram_probe=NVMLVRAMProbe(config.gpu.index))
    uvicorn.run(app, host=args.host, port=args.port)
