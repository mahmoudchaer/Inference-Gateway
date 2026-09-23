import os

from inference_gateway.config import read_config, write_config
from inference_gateway.routing import (
    disable_codex,
    enable_codex,
    ensure_codex,
    routing_status,
)


def test_codex_routing_round_trip_preserves_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path / "data"))
    config = tmp_path / "config.toml"
    original = 'model = "gpt-5"\n\n[projects."C:\\\\work"]\ntrust_level = "trusted"\n'
    config.write_text(original)
    enable_codex("http://127.0.0.1:8080/v1", path=config)
    changed = config.read_text()
    assert changed.index("openai_base_url") < changed.index("[projects")
    assert routing_status(path=config) == "http://127.0.0.1:8080/v1"
    assert disable_codex(path=config)
    assert config.read_text() == original


def test_routing_restores_previous_value_and_preserves_later_edits(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path / "data"))
    config = tmp_path / "config.toml"
    config.write_text('openai_base_url = "https://example.test/v1"\nmodel = "old"\n')
    enable_codex("http://127.0.0.1:8080/v1", path=config)
    config.write_text(config.read_text().replace('model = "old"', 'model = "new"'))
    assert disable_codex(path=config)
    assert 'openai_base_url = "https://example.test/v1"' in config.read_text()
    assert 'model = "new"' in config.read_text()


def test_does_not_overwrite_user_routing_change(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path / "data"))
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5"\n')
    enable_codex("http://127.0.0.1:8080/v1", path=config)
    config.write_text(config.read_text().replace("127.0.0.1:8080", "127.0.0.1:9999"))
    assert not disable_codex(path=config)
    assert "127.0.0.1:9999" in config.read_text()


def test_repair_then_restart_does_not_back_up_the_gateway_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path / "data"))
    config = tmp_path / "config.toml"
    original = 'openai_base_url = "https://original.test/v1"\n'
    config.write_text(original)
    enable_codex("http://127.0.0.1:8080/v1", path=config)
    assert disable_codex(path=config)
    enable_codex("http://127.0.0.1:8080/v1", path=config)
    assert disable_codex(path=config)
    assert config.read_text() == original


def test_ensure_codex_reconnects_a_removed_route(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path / "data"))
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5"\n')
    gateway = "http://127.0.0.1:8080/v1"

    assert ensure_codex(gateway, path=config)
    assert routing_status(path=config) == gateway
    assert not ensure_codex(gateway, path=config)


def test_saved_config_is_private_and_secret_is_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path))
    write_config({"optimization": "high", "logs_enabled": False, "jev_api_key": "secret"})
    assert read_config()["optimization"] == "high"
    assert "jev_api_key" not in read_config()
    assert read_config(include_secret=True)["jev_api_key"] == "secret"
    if os.name != "nt":
        assert (tmp_path / "config.json").stat().st_mode & 0o777 == 0o600
