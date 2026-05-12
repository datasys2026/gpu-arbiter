from __future__ import annotations

import os
import pathlib
import re
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


class HealthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["http"] = "http"
    url: str
    method: str = "GET"
    wait_timeout_seconds: float = Field(default=60, gt=0)
    poll_interval_seconds: float = Field(default=2.0, gt=0)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str
    upstream: str
    uses_gpu: bool = True
    required_vram_mb: int = Field(default=0, ge=0)
    health: HealthConfig | None = None
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


_UNEXPANDED_RE = re.compile(r"\$\{[^}]+\}")


def _expand_environment(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    return value


def _assert_no_unexpanded(value: Any, path: str = "") -> None:
    if isinstance(value, str):
        m = _UNEXPANDED_RE.search(value)
        if m:
            raise ValueError(
                f"Unresolved env var {m.group()!r} in config"
                + (f" at {path!r}" if path else "")
                + ". Set the environment variable before starting."
            )
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _assert_no_unexpanded(item, f"{path}[{i}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_no_unexpanded(item, f"{path}.{key}" if path else key)


def load_config(path: str | pathlib.Path) -> ArbiterConfig:
    raw = yaml.safe_load(pathlib.Path(path).read_text()) or {}
    raw = {k: v for k, v in raw.items() if not k.startswith("x-")}
    raw = _expand_environment(raw)
    _assert_no_unexpanded(raw)
    return ArbiterConfig.model_validate(raw)
