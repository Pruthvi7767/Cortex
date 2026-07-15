import httpx
import asyncio

API_URL = "http://127.0.0.1:8000/v1/complete"
API_KEY = "sk-cortex-0ygtCEspCVoyrjQPgdnvrxDeNAkrWOkr"

async def main():
    headers = {"Authorization": f"Bearer {API_KEY}"}
    payload = {"prompt": "What is 2+2?"}
    async with httpx.AsyncClient() as client:
        res = await client.post(API_URL, json=payload, headers=headers)
        print(f"Status: {res.status_code}")
        print(f"Body: {res.text}")

asyncio.run(main())
