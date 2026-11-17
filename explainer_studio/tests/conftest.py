import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The app on a fresh, empty data folder."""
    from app import config
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(config, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(config, "VOICE_CACHE", tmp_path / "voice-cache")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def can_render() -> bool:
    from app import config
    return (config.EV_HOME / "models/kokoro-v1.0.int8.onnx").exists() and pathlib.Path(config.PY).exists()
