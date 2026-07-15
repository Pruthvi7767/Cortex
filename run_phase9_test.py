import asyncio
import json
import time
import httpx
import os
import uuid
import sys

API_URL = "http://127.0.0.1:8000/v1/complete"
DB_DSN = "postgresql://postgres:postgres@localhost:5433/postgres"
API_KEY = "sk-cortex-0ygtCEspCVoyrjQPgdnvrxDeNAkrWOkr"

# We will construct 100 test cases:
# 30 simple (fast)
# 30 complex (strong)
# 20 ambiguous (mid/llm-classifier)
# 10 manual tier override
# 10 tool call tests (2 with invalid hallucinated tools)

def generate_test_cases():
    cases = []
    
    # 1-30: Simple (fast)
    simple_prompts = [
        "What is 2+2?", "Define gravity.", "Translate 'hello' to French.", 
        "Summarize in one line: water is wet.", "Say hello to the world.",
        "What is the capital of France?", "Hi!", "Ping.", 
        "What color is the sky?", "Define 'apple'."
    ] * 3 # 30 total
    for i, p in enumerate(simple_prompts):
        cases.append({"id": f"simple_{i}", "prompt": p})
        
    # 31-60: Complex (strong)
    complex_prompts = [
        "Write the final client proposal for our new cloud architecture migration project.",
        "Analyze the step by step process to deploy a Kubernetes cluster on AWS.",
        "Compare and evaluate the architectural differences between monolithic and microservices.",
        "Synthesize a report on the economic impact of AI in 2024.",
        "Debug the following python code snippet step by step: def foo(): return 1/0"
    ] * 6 # 30 total
    for i, p in enumerate(complex_prompts):
        cases.append({"id": f"complex_{i}", "prompt": p})
        
    # 61-80: Ambiguous (tests Pulse LLM-classifier, not explicitly simple/complex)
    ambiguous_prompts = [
        "How do I cook pasta?",
        "What are some good books to read?",
        "Tell me a story about a dragon.",
        "How to make a cup of coffee.",
        "Can you recommend a good movie?",
        "I am feeling sad today.",
        "What's a good workout routine?",
        "Write a poem about the ocean.",
        "How do I tie a tie?",
        "Why is the grass green?"
    ] * 2 # 20 total
    for i, p in enumerate(ambiguous_prompts):
        cases.append({"id": f"ambiguous_{i}", "prompt": p})
        
    # 81-90: Manual tier override
    for i in range(10):
        cases.append({
            "id": f"manual_{i}", 
            "prompt": f"This is manual test prompt {i}.",
            "tier": "strong" if i % 3 == 0 else "mid" if i % 3 == 1 else "fast"
        })
        
    # 91-100: Tool call tests
    valid_tool = {
        "name": "get_weather",
        "description": "Get the weather in a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"]
        }
    }
    
    # 8 valid tool calls
    for i in range(8):
        cases.append({
            "id": f"tool_valid_{i}",
            "prompt": "What is the weather in Seattle?",
            "tools": [valid_tool],
            "tool_whitelist": ["get_weather"],
            "expected_tool_schema": valid_tool["parameters"]
        })
        
    # 2 invalid tool calls (hallucination rejection)
    for i in range(2):
        cases.append({
            "id": f"tool_invalid_{i}",
            "prompt": "What is the weather in Seattle?",
            "tools": [valid_tool],
            "tool_whitelist": ["nonexistent_tool"], # Hallucinated rejection
            "expected_tool_schema": valid_tool["parameters"]
        })
        
    return cases

async def main():
    import asyncpg
    
    # 1. Generate or fetch an API key
    # For this script we assume the user provides one or we connect to DB to get one.
    print("Connecting to DB to fetch an API key...")
    try:
        pool = await asyncpg.create_pool(DB_DSN)
    except Exception as e:
        print(f"Failed to connect to Postgres: {e}")
        sys.exit(1)
        
    async with pool.acquire() as conn:
        key_record = await conn.fetchrow("SELECT caller_id FROM api_keys LIMIT 1")
        if not key_record:
            print("No API keys found. Please run create_api_key.py first.")
            sys.exit(1)
        
    # Wait, the DB only stores hashes! We cannot retrieve the raw key from DB.
    # We must require it from the user.
    raw_key = os.environ.get("CORTEX_API_KEY")
    if not raw_key:
        print("Set CORTEX_API_KEY environment variable to a valid key to run the test.")
        sys.exit(1)
        
    cases = generate_test_cases()
    results = []
    
    headers = {"Authorization": f"Bearer {raw_key}"}
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        for i, case in enumerate(cases):
            print(f"--- Running test {i+1}/100: {case['id']} ---")
            payload = {"prompt": case["prompt"]}
            if "tier" in case:
                payload["tier"] = case["tier"]
            if "tools" in case:
                payload["tools"] = case["tools"]
                payload["tool_whitelist"] = case["tool_whitelist"]
                payload["expected_tool_schema"] = case["expected_tool_schema"]
                
            start_time = time.time()
            try:
                res = await client.post(API_URL, json=payload, headers=headers)
                wallclock_ms = int((time.time() - start_time) * 1000)
                
                try:
                    resp_json = res.json()
                except Exception as e:
                    print(f"Exception during request: {e}, Status: {res.status_code}, Body: {res.text}")
                    continue
                telemetry = resp_json.get("telemetry", {})
                
                request_id = resp_json.get("request_id")
                
                # We need to wait a tiny bit to ensure the background log_request task has run
                await asyncio.sleep(0.5)
                
                # Now fetch the log from Postgres
                async with pool.acquire() as conn:
                    if request_id:
                        log_row = await conn.fetchrow("SELECT * FROM requests_log WHERE request_id = $1", request_id)
                    else:
                        # Fallback for errors that don't return request_id
                        log_row = await conn.fetchrow("SELECT * FROM requests_log ORDER BY created_at DESC LIMIT 1")
                
                if log_row:
                    record = {
                        "question_number": i + 1,
                        "prompt": case["prompt"],
                        "tier_requested": log_row["tier_requested"],
                        "tier_source": log_row["tier_source"],
                        "pulse_decision_score": log_row["decision_score"],
                        "pulse_used_llm_classifier": telemetry.get("used_llm_classifier"),
                        "provider_used": log_row["provider_used"],
                        "model_used": log_row["model_used"],
                        "nvidia_attempted": log_row["nvidia_attempted"] or False,
                        "nvidia_succeeded": log_row["nvidia_succeeded"],
                        "latency_ms_logged": log_row["latency_ms"],
                        "latency_ms_wallclock": wallclock_ms,
                        "success": log_row["success"],
                        "error_type": log_row["error_type"],
                        "retry_triggered": telemetry.get("retry_triggered", False),
                        "prompt_tokens": log_row["prompt_tokens"],
                        "completion_tokens": log_row["completion_tokens"],
                        "total_tokens": log_row["total_tokens"],
                        "response_content": resp_json.get("content"),
                        "tool_call_returned": resp_json.get("tool_calls"),
                        "validation_rejection": log_row["validation_rejections"],
                        "circuit_breaker_tripped_this_request": False, # inferred for test
                        "quota_exhausted_this_request": False
                    }
                    results.append(record)
                else:
                    print("Log row not found.")
                
            except Exception as e:
                print(f"Exception during request: {e}")
                
    with open("phase9_results.json", "w") as f:
        json.dump(results, f, indent=2)
        
    # Generate summary
    successes = [r for r in results if r["success"]]
    summary = f"# Phase 9 Test Summary\\n\\n"
    summary += f"- Total Requests: {len(results)}\\n"
    summary += f"- Success Rate: {len(successes)}/{len(results)}\\n"
    avg_latency = sum(r["latency_ms_wallclock"] for r in results) / len(results) if results else 0
    summary += f"- Avg Wallclock Latency: {avg_latency:.0f}ms\\n"
    
    with open("phase9_summary.md", "w") as f:
        f.write(summary)
        
    print("Test complete. Results saved to phase9_results.json and phase9_summary.md.")

if __name__ == "__main__":
    asyncio.run(main())
