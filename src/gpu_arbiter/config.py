from __future__ import annotations

import os
import pathlib
from typing import Any

import yaml
from pydantic import BaseModel, Field


class GPUConfig(BaseModel):
    index: int = 0
    cooldown_seconds: float = 0


class HookConfig(BaseModel):
    type: str = "http"
    url: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 30
    wait_timeout_seconds: float = 120


class ModelConfig(BaseModel):
    route: str
    upstream: str
    required_vram_mb: int = Field(default=0, ge=0)
    health: HookConfig | None = None
    unload: HookConfig | None = None


class ArbiterConfig(BaseModel):
    gpu: GPUConfig = Field(default_factory=GPUConfig)
    models: dict[str, ModelConfig]


def _expand_environment(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    return value


def load_config(path: str | pathlib.Path) -> ArbiterConfig:
    raw = yaml.safe_load(pathlib.Path(path).read_text()) or {}
    raw = _expand_environment(raw)
    return ArbiterConfig.model_validate(raw)
