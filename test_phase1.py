import os
import pytest
from fastapi.testclient import TestClient
import config
from main import app

client = TestClient(app)

def test_one_provider(monkeypatch):
    monkeypatch.setattr(config.os, "getenv", lambda k, default=None: "dummy_key" if k == "NVIDIA_API_KEY" else default)
    active = config.get_active_providers()
    assert len(active) == 1
    assert active[0]["id"] == "nvidia"

def test_all_providers(monkeypatch):
    def mock_getenv(k, default=None):
        if k.endswith("_API_KEY") or k == "CLOUDFLARE_ACCOUNT_ID":
            return "dummy_val"
        return default
    monkeypatch.setattr(config.os, "getenv", mock_getenv)
    active = config.get_active_providers()
    assert len(active) == 24  # 24 providers in the list

def test_zero_providers(monkeypatch):
    monkeypatch.setattr(config.os, "getenv", lambda k, default=None: default)
    active = config.get_active_providers()
    assert len(active) == 0
    with pytest.raises(RuntimeError):
        config.assert_providers_configured()

def test_empty_provider(monkeypatch, caplog):
    def mock_getenv(k, default=None):
        if k == "NVIDIA_API_KEY":
            return "" # empty string
        return default
    monkeypatch.setattr(config.os, "getenv", mock_getenv)
    active = config.get_active_providers()
    assert len(active) == 0
    assert "Environment variable NVIDIA_API_KEY exists but is empty" in caplog.text
