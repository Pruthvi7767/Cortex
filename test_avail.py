import asyncio
from config import MODEL_REGISTRY
from router import check_availability

async def main():
    fast_models = MODEL_REGISTRY.get("fast", [])
    for m in fast_models:
        avail = await check_availability(m["provider"], m["model_id"])
        print(f"{m['provider']}/{m['model_id']}: {avail}")

if __name__ == "__main__":
    asyncio.run(main())
