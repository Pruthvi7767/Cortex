from contextlib import asynccontextmanager
from fastapi import FastAPI
from config import get_active_providers, assert_providers_configured, environment

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    assert_providers_configured()
    yield
    # Shutdown logic
    pass

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health_check():
    active_providers = get_active_providers()
    provider_ids = [p["id"] for p in active_providers]
    
    return {
        "status": "ok",
        "environment": environment,
        "active_providers_count": len(active_providers),
        "active_providers": provider_ids
    }
