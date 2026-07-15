import asyncio
import httpx
from provider_adapters import get_endpoint_url, get_auth_headers

async def main():
    headers = get_auth_headers("groq")
    url = get_endpoint_url("groq", "allam-2-7b")
    body = {
        "model": "allam-2-7b",
        "messages": [{"role": "user", "content": "Hi!"}]
    }
    # async with httpx.AsyncClient() as client:
    #     resp = await client.post(url, headers=headers, json=body)
    #     print("Groq response:", resp.status_code, resp.text.encode('utf-8'))
        
    headers = get_auth_headers("cloudflare")
    url = get_endpoint_url("cloudflare", "@cf/mistralai/mistral-small-3.1-24b-instruct")
    body = {
        "model": "@cf/mistralai/mistral-small-3.1-24b-instruct",
        "messages": [{"role": "user", "content": "Hi!"}]
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=body)
        print("Cloudflare response:", resp.status_code, resp.text)

if __name__ == "__main__":
    asyncio.run(main())
