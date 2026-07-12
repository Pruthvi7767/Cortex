import os
import logging
from typing import Dict, List
from dotenv import load_dotenv

# Setup minimal logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cortex.config")

# Determine environment and load appropriate .env
environment = os.getenv("ENVIRONMENT", "development")
env_file = f".env.{environment}"

if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv(".env")

# Tier definitions and timeouts
TIER_TIMEOUTS = {
    "fast": 1.5,
    "mid": 3.0,
    "strong": 5.0
}

# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Quota limits (daily limit of requests/tokens depending on provider semantics)
QUOTA_LIMITS = {
    "nvidia": 999999999,   # RPM limited, practically no daily cap
    "cerebras": 1000000,   # ~1M tokens
    "groq": 10000,         # Request count placeholder
    "google": 1500,        # ~1500 requests
    # Default for all others
    "default": 5000
}

# RPM limits
RPM_LIMITS = {
    "nvidia": 60,
    "cerebras": 60,
    "groq": 30,
    "google": 15,
    "default": 30
}

# Provider Registry (function to allow future hot-reloading)
def get_provider_registry() -> List[Dict]:
    """
    Returns the complete provider registry.
    """
    return [
        {"id": "nvidia", "env_var": "NVIDIA_API_KEY", "tier": 1, "base_url": "https://integrate.api.nvidia.com/v1"},
        {"id": "groq", "env_var": "GROQ_API_KEY", "tier": 1, "base_url": "https://api.groq.com/openai/v1"},
        {"id": "cerebras", "env_var": "CEREBRAS_API_KEY", "tier": 1, "base_url": "https://api.cerebras.ai/v1"},
        {"id": "google", "env_var": "GOOGLE_API_KEY", "tier": 1, "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
        {"id": "mistral", "env_var": "MISTRAL_API_KEY", "tier": 1, "base_url": "https://api.mistral.ai/v1"},
        {"id": "cloudflare", "env_var": "CLOUDFLARE_API_KEY", "tier": 1, "base_url": "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1"},
        {"id": "ollama", "env_var": "OLLAMA_API_KEY", "tier": 1, "base_url": "http://localhost:11434/v1"},
        
        {"id": "github", "env_var": "GITHUB_API_KEY", "tier": 2, "base_url": "https://models.inference.ai.azure.com"},
        {"id": "hf", "env_var": "HF_API_KEY", "tier": 2, "base_url": "https://api-inference.huggingface.co/v1/"},
        {"id": "siliconflow", "env_var": "SILICONFLOW_API_KEY", "tier": 2, "base_url": "https://api.siliconflow.cn/v1"},
        {"id": "modelscope", "env_var": "MODELSCOPE_API_KEY", "tier": 2, "base_url": "https://api-inference.modelscope.cn/v1"},
        {"id": "alibaba", "env_var": "ALIBABA_API_KEY", "tier": 2, "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        {"id": "zhipu", "env_var": "ZHIPU_API_KEY", "tier": 2, "base_url": "https://open.bigmodel.cn/api/paas/v4/"},
        {"id": "sambanova", "env_var": "SAMBANOVA_API_KEY", "tier": 2, "base_url": "https://api.sambanova.ai/v1"},
        
        {"id": "kilocode", "env_var": "KILOCODE_API_KEY", "tier": 3, "base_url": ""},
        {"id": "opencodezen", "env_var": "OPENCODEZEN_API_KEY", "tier": 3, "base_url": ""},
        {"id": "llm7", "env_var": "LLM7_API_KEY", "tier": 3, "base_url": ""},
        {"id": "chutes", "env_var": "CHUTES_API_KEY", "tier": 3, "base_url": ""},
        {"id": "glhf", "env_var": "GLHF_API_KEY", "tier": 3, "base_url": "https://glhf.chat/api/openai/v1"},
        {"id": "aionlabs", "env_var": "AIONLABS_API_KEY", "tier": 3, "base_url": ""},
        {"id": "agnes", "env_var": "AGNES_API_KEY", "tier": 3, "base_url": ""},
        {"id": "nscale", "env_var": "NSCALE_API_KEY", "tier": 3, "base_url": ""},
        {"id": "neibius", "env_var": "NEIBIUS_API_KEY", "tier": 3, "base_url": ""},
        {"id": "ovhcloud", "env_var": "OVHCLOUD_API_KEY", "tier": 3, "base_url": ""}
    ]

def get_active_providers() -> List[Dict]:
    """
    Returns only the providers that have a non-empty API key set in the environment.
    Logs warnings for explicitly empty/malformed keys.
    """
    registry = get_provider_registry()
    active_providers = []

    for provider in registry:
        env_var_name = provider["env_var"]
        # Treat None as not set. Treat "" as explicitly empty/malformed.
        val = os.getenv(env_var_name)
        
        if val is None:
            # Not set at all, skip silently
            continue
        elif str(val).strip() == "":
            # Set but empty/malformed
            logger.warning(f"Provider {provider['id']} skipped: Environment variable {env_var_name} exists but is empty.")
            continue
        else:
            # Set and non-empty
            # Special case for cloudflare to ensure ACCOUNT_ID is also present
            if provider["id"] == "cloudflare":
                cf_account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
                if not cf_account or str(cf_account).strip() == "":
                    logger.warning(f"Provider cloudflare skipped: CLOUDFLARE_API_KEY is set but CLOUDFLARE_ACCOUNT_ID is missing or empty.")
                    continue
            
            active_providers.append(provider)

    return active_providers

def assert_providers_configured():
    """
    Ensures at least one provider is active, else raises RuntimeError.
    Called at app startup.
    """
    active = get_active_providers()
    if not active:
        raise RuntimeError("CRITICAL STARTUP FAILURE: Zero active providers configured. Please set at least one provider API key in your environment.")
    
    provider_ids = [p["id"] for p in active]
    logger.info(f"Cortex booted with {len(active)} active providers: {', '.join(provider_ids)}")
