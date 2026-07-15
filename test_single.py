import asyncio
import traceback
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

from main import app, get_caller, check_rate_limit

# Mock authentication
async def mock_get_caller():
    return {"caller_id": "stress_tester", "rate_limit_per_minute": 1000}

async def mock_check_rate_limit():
    return True

app.dependency_overrides[get_caller] = mock_get_caller
app.dependency_overrides[check_rate_limit] = mock_check_rate_limit

async def run_single():
    with patch("main.execute_with_retry", new_callable=AsyncMock) as mock_execute:
        class MockResult:
            provider_used = "mocked"
            model_used = "mocked"
            latency_ms = 10
            success = True
            error_type = None
            prompt_tokens = 10
            completion_tokens = 20
            total_tokens = 30
            content = "Mocked LLM Response"
            tool_calls = None
        mock_execute.return_value = MockResult()
        
        with patch("main.log_request", new_callable=AsyncMock):
            async with AsyncClient(app=app, base_url="http://test") as client:
                try:
                    resp = await client.post("/v1/complete", json={
                        "prompt": "Test prompt",
                        "idempotency_key": "test-1"
                    })
                    print(resp.status_code)
                except Exception as e:
                    traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_single())
