import pytest
import asyncio
import time
from unittest.mock import AsyncMock, patch

from main import complete_endpoint, CompleteRequest

@pytest.mark.asyncio
async def test_api_100_concurrent_direct():
    print("\n--- Starting 100 Request API Stress Test (Direct Function Call) ---")
    
    with patch("main.execute_with_retry", new_callable=AsyncMock) as mock_execute:
        # Mock the result structure returned by execute_with_retry
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
            retry_triggered = False
            nvidia_attempted = False
            nvidia_succeeded = False
            validation_rejections = 0
            
        mock_execute.return_value = MockResult()
        
        # Patch external IO
        with patch("main.log_request", new_callable=AsyncMock):
            with patch("classifier.compute_embedding_score", new_callable=AsyncMock) as mock_embed:
                with patch("redis_store.client.get", new_callable=AsyncMock) as mock_redis_get:
                    with patch("redis_store.client.set", new_callable=AsyncMock):
                        mock_embed.return_value = 0.5
                        mock_redis_get.return_value = None
                        
                        requests_to_make = 100
                        caller_info = {"caller_id": "stress_tester", "rate_limit_per_minute": 1000}
                        
                        tasks = []
                        for i in range(requests_to_make):
                            req = CompleteRequest(
                                prompt=f"This is stress test prompt {i} checking for bugs.",
                                idempotency_key=f"stress-{i}"
                            )
                            # complete_endpoint returns a dict or raises HTTPException
                            tasks.append(complete_endpoint(req=req, caller_info=caller_info))
                        
                        start_time = time.perf_counter()
                        
                        # Execute all concurrently
                        responses = await asyncio.gather(*tasks, return_exceptions=True)
                        
                        elapsed = time.perf_counter() - start_time
                        
                        success_count = 0
                        error_count = 0
                        
                        for i, resp in enumerate(responses):
                            if isinstance(resp, Exception):
                                print(f"Exception on request {i}: {type(resp).__name__}: {str(resp)}")
                                error_count += 1
                            else:
                                # successful request returns a dict
                                success_count += 1
                                
                        print("\n--- Results ---")
                        print(f"Total requests: {requests_to_make}")
                        print(f"Success: {success_count}")
                        print(f"Errors: {error_count}")
                        print(f"Elapsed time: {elapsed:.2f}s")
                        print(f"Requests/sec: {requests_to_make / elapsed:.2f}")
                        
                        assert success_count == 100
