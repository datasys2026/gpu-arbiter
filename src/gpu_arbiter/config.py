from __future__ import annotations

import os
import pathlib
from typing import Any
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class GPUConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = 0
    cooldown_seconds: float = 0


class HookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["http"] = "http"
    url: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body_json: Any | None = None
    timeout_seconds: float = Field(default=30, gt=0)
    wait_timeout_seconds: float = Field(default=120, gt=0)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str
    upstream: str
    uses_gpu: bool = True
    required_vram_mb: int = Field(default=0, ge=0)
    health: HookConfig | None = None
    unload: HookConfig | list[HookConfig] | None = None


class ArbiterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gpu: GPUConfig = Field(default_factory=GPUConfig)
    models: dict[str, ModelConfig]

    @model_validator(mode="after")
    def _check_models_not_empty(self) -> "ArbiterConfig":
        if not self.models:
            raise ValueError("models dict must not be empty")
        return self


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
    raw = {k: v for k, v in raw.items() if not k.startswith("x-")}
    raw = _expand_environment(raw)
    return ArbiterConfig.model_validate(raw)
