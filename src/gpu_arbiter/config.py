from __future__ import annotations

import pathlib

import yaml
from pydantic import BaseModel, Field


class GPUConfig(BaseModel):
    index: int = 0
    cooldown_seconds: float = 0


class HookConfig(BaseModel):
    type: str = "http"
    url: str
    method: str = "POST"
    timeout_seconds: float = 30


class ModelConfig(BaseModel):
    route: str
    upstream: str
    required_vram_mb: int = Field(default=0, ge=0)
    health: HookConfig | None = None
    unload: HookConfig | None = None


class ArbiterConfig(BaseModel):
    gpu: GPUConfig = Field(default_factory=GPUConfig)
    models: dict[str, ModelConfig]


def load_config(path: str | pathlib.Path) -> ArbiterConfig:
    raw = yaml.safe_load(pathlib.Path(path).read_text()) or {}
    return ArbiterConfig.model_validate(raw)
