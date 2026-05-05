import pytest

from gpu_arbiter.config import load_config


def test_load_config_maps_models(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
gpu:
  index: 0
  cooldown_seconds: 2
models:
  aiark/z-image-turbo:
    route: /v1/images/generations
    upstream: http://image-api:8003
    required_vram_mb: 12000
    unload:
      type: http
      url: http://image-api:8003/admin/unload
""",
    )

    config = load_config(config_path)

    assert config.gpu.index == 0
    assert config.gpu.cooldown_seconds == 2
    assert config.models["aiark/z-image-turbo"].upstream == "http://image-api:8003"
    assert config.models["aiark/z-image-turbo"].required_vram_mb == 12000


def test_load_config_expands_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_API_KEY", "test-key")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
models:
  aiark/z-image-turbo:
    route: /v1/images/generations
    upstream: http://image-api:8003
    unload:
      url: http://image-api:8003/admin/unload
      headers:
        Authorization: Bearer ${IMAGE_API_KEY}
""",
    )

    config = load_config(config_path)

    unload = config.models["aiark/z-image-turbo"].unload
    assert unload is not None
    assert unload.headers["Authorization"] == "Bearer test-key"


def test_load_config_accepts_multiple_unload_hooks_with_json_body(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
models:
  aiark/z-image-turbo:
    route: /v1/images/generations
    upstream: http://image-api:8003
    unload:
      - url: http://ollama:11434/api/generate
        body_json:
          model: gemma4:e2b
          keep_alive: 0
      - url: http://image-api:8003/admin/unload
""",
    )

    config = load_config(config_path)

    unload = config.models["aiark/z-image-turbo"].unload
    assert isinstance(unload, list)
    assert unload[0].body_json == {"model": "gemma4:e2b", "keep_alive": 0}
    assert unload[1].url == "http://image-api:8003/admin/unload"
