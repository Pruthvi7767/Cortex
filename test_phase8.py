import pytest
from provider_adapters import parse_response, ParseError, build_request, get_endpoint_url
import os

def test_cloudflare_adapter():
    """Test Cloudflare Workers AI schema handling."""
    provider = "cloudflare"
    model_id = "@cf/meta/llama-3-8b-instruct"
    
    # 1. Test Endpoint URL resolution
    os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test_account_123"
    # Assuming config.py is loaded properly, but since we are just testing the logic,
    # if it tries to load config, it needs the environment.
    # Let's test parse_response first which is purely isolated.
    
    # 2. Test successful parse_response
    raw_response_success = {
        "choices": [{
            "message": {
                "content": "Hello from Cloudflare!"
            }
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5
        }
    }
    
    parsed = parse_response(provider, raw_response_success)
    assert parsed["content"] == "Hello from Cloudflare!"
    assert parsed["tool_calls"] is None
    assert parsed["usage"]["prompt_tokens"] == 10
    assert parsed["usage"]["completion_tokens"] == 5
    
    # 3. Test failure / unexpected schema
    raw_response_fail = {
        "result": {
            # missing "response"
            "other_stuff": "..."
        },
        "success": True
    }
    
    with pytest.raises(ParseError) as exc:
        parse_response(provider, raw_response_fail)
    assert "'choices' missing" in str(exc.value)

    # 4. Test missing result entirely
    with pytest.raises(ParseError) as exc:
        parse_response(provider, {"success": False})
    assert "'choices' missing" in str(exc.value)

    # 5. Test build_request structure
    req = build_request(
        provider=provider,
        model_id=model_id,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100
    )
    # Cloudflare now expects standard OpenAI schema, which includes 'model'
    assert "model" in req
    assert req["model"] == model_id
    assert req["messages"] == [{"role": "user", "content": "hi"}]
    assert req["max_tokens"] == 100
