import asyncio
import httpx
from provider_adapters import get_endpoint_url, get_auth_headers

async def main():
    headers = get_auth_headers("cloudflare")
    url = get_endpoint_url("cloudflare", "@cf/qwen/qwen2.5-coder-32b-instruct")
    body = {
        "model": "@cf/qwen/qwen2.5-coder-32b-instruct",
        "messages": [{"role": "user", "content": "Debug the following python code snippet step by step: def foo(): return 1/0"}],
        "max_tokens": None,
        "stream": False
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        print("Cloudflare Qwen response:", resp.status_code, resp.text)

if __name__ == "__main__":
    asyncio.run(main())
