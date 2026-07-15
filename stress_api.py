import asyncio
import time
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from main import app, get_caller, check_rate_limit

# Mock authentication
async def mock_get_caller():
    return {"caller_id": "stress_tester", "rate_limit_per_minute": 1000}

async def mock_check_rate_limit():
    return True

app.dependency_overrides[get_caller] = mock_get_caller
app.dependency_overrides[check_rate_limit] = mock_check_rate_limit

client = TestClient(app)

async def test_api_100():
    print("\n--- Starting 100 Request API Stress Test ---")
    
    # We will mock execute_race to avoid hitting actual LLM endpoints
    with patch("main.execute_race", new_callable=AsyncMock) as mock_execute_race:
        
        mock_execute_race.return_value = {
            "content": "Mocked LLM Response",
            "tool_calls": None,
            "provider": "mocked",
            "model": "mocked",
            "latency_ms": 10
        }
        
        requests_to_make = 100
        success_count = 0
        error_count = 0
        
        start_time = time.perf_counter()
        
        for i in range(requests_to_make):
            try:
                # We vary the prompt slightly
                resp = client.post("/v1/complete", json={
                    "prompt": f"This is stress test prompt {i} checking for bugs.",
                    "idempotency_key": f"stress-{i}"
                })
                if resp.status_code == 200:
                    success_count += 1
                else:
                    print(f"Error on request {i}: {resp.status_code} - {resp.text}")
                    error_count += 1
            except Exception as e:
                print(f"Exception on request {i}: {str(e)}")
                error_count += 1
                
        elapsed = time.perf_counter() - start_time
        
        print("\n--- Results ---")
        print(f"Total requests: {requests_to_make}")
        print(f"Success: {success_count}")
        print(f"Errors: {error_count}")
        print(f"Elapsed time: {elapsed:.2f}s")
        print(f"Requests/sec: {requests_to_make / elapsed:.2f}")

if __name__ == "__main__":
    asyncio.run(test_api_100())
