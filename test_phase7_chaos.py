import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from main import app
from race import RaceResult

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_auth():
    with patch("main.verify_api_key", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = {"caller_id": "test_caller", "rate_limit_per_minute": 100}
        with patch("main.check_caller_rate_limit", new_callable=AsyncMock) as mock_rate:
            mock_rate.return_value = True
            yield mock_verify

@pytest.fixture
def mock_race():
    with patch("main.execute_race", new_callable=AsyncMock) as mock_er:
        yield mock_er

def test_scenario_1_successful_completion(mock_race):
    mock_race.return_value = RaceResult(
        success=True, content="Hello", tool_calls=None,
        model_used="model_A", provider_used="prov_A",
        latency_ms=100.0, error_type=None
    )
    resp = client.post("/v1/complete", json={"prompt": "test"}, headers={"Authorization": "Bearer testkey"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "Hello"

def test_scenario_2_provider_timeout_and_retry(mock_race):
    # First call fails with timeout, second succeeds
    mock_race.side_effect = [
        RaceResult(
            success=False, content=None, tool_calls=None,
            model_used=None, provider_used=None,
            latency_ms=5000.0, error_type="timeout"
        ),
        RaceResult(
            success=True, content="Recovery", tool_calls=None,
            model_used="model_B", provider_used="prov_B",
            latency_ms=150.0, error_type=None
        )
    ]
    with patch("asyncio.sleep", new_callable=AsyncMock):
        resp = client.post("/v1/complete", json={"prompt": "test"}, headers={"Authorization": "Bearer testkey"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "Recovery"
    assert mock_race.call_count == 2

def test_scenario_3_all_candidates_failed(mock_race):
    mock_race.return_value = RaceResult(
        success=False, content=None, tool_calls=None,
        model_used=None, provider_used=None,
        latency_ms=5000.0, error_type="all_candidates_failed"
    )
    with patch("asyncio.sleep", new_callable=AsyncMock):
        resp = client.post("/v1/complete", json={"prompt": "test"}, headers={"Authorization": "Bearer testkey"})
    assert resp.status_code == 500
    assert resp.json()["detail"]["error_type"] == "all_candidates_failed"

def test_scenario_4_idempotency():
    with patch("main.redis_client.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch("main.redis_client.set", new_callable=AsyncMock) as mock_set:
            with patch("main.execute_with_retry", new_callable=AsyncMock) as mock_retry:
                mock_retry.return_value = RaceResult(
                    success=True, content="Idem", tool_calls=None,
                    model_used="model_A", provider_used="prov_A",
                    latency_ms=100.0, error_type=None
                )
                resp1 = client.post("/v1/complete", json={"prompt": "test", "idempotency_key": "abc"}, headers={"Authorization": "Bearer testkey"})
                assert resp1.status_code == 200

    with patch("main.redis_client.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = b"processing"
        resp2 = client.post("/v1/complete", json={"prompt": "test", "idempotency_key": "abc"}, headers={"Authorization": "Bearer testkey"})
        assert resp2.status_code == 409

def test_scenario_5_rate_limit_exceeded():
    with patch("main.check_caller_rate_limit", new_callable=AsyncMock) as mock_rate:
        mock_rate.return_value = False
        resp = client.post("/v1/complete", json={"prompt": "test"}, headers={"Authorization": "Bearer testkey"})
        assert resp.status_code == 429

def test_scenario_6_missing_prompt():
    resp = client.post("/v1/complete", json={}, headers={"Authorization": "Bearer testkey"})
    assert resp.status_code == 422 

def test_scenario_7_prompt_too_long():
    long_prompt = "a" * 500001
    resp = client.post("/v1/complete", json={"prompt": long_prompt}, headers={"Authorization": "Bearer testkey"})
    assert resp.status_code == 413

def test_scenario_8_auth_failure():
    with patch("main.verify_api_key", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = None
        resp = client.post("/v1/complete", json={"prompt": "test"}, headers={"Authorization": "Bearer badkey"})
        assert resp.status_code == 401
