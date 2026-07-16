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

# Tier definitions and timeouts (seconds per model attempt)
TIER_TIMEOUTS = {
    "fast": 1.5,
    "mid": 3.0,
    "strong": 5.0
}

# The number of non-NVIDIA candidates to race in parallel if the NVIDIA first-shot fails
FALLBACK_RACE_WIDTH = 5

# Short timeout for the NVIDIA-first single-shot attempt before fanning out to others.
# NVIDIA has no daily cap so we can afford an aggressive cutoff here.
NVIDIA_FIRST_TIMEOUT = 2.0

# Dedicated classifier models for Pulse Layer 2b (hedged request pattern).
# One per provider — cheapest/fastest model available.
# Priority order matters: first healthy entry = PRIMARY, second = BACKUP.
# NVIDIA first (no daily token cap), then Groq, Cerebras, Google, Mistral, Cloudflare, Ollama.
# These are ONLY used by Pulse — never included in routing decisions.
CLASSIFIER_MODELS = [
    {"provider": "nvidia",     "model_id": "nvidia/nemotron-mini-4b-instruct"},
    {"provider": "groq",       "model_id": "llama-3.1-8b-instant"},
    {"provider": "cerebras",   "model_id": "llama3.1-8b"},
    {"provider": "google",     "model_id": "gemini-2.5-flash-lite"},
    {"provider": "mistral",    "model_id": "ministral-3b-latest"},
    {"provider": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct-fp8"},
    {"provider": "ollama",     "model_id": "gpt-oss:20b"},
]

# Dedicated Embedding model for Pulse Layer 3 (Embedding Ensemble).
# This is called concurrently with Layer 2b. We default to Google (Gemini).
EMBEDDING_PROVIDER = "google"
EMBEDDING_MODEL = "text-embedding-004"

# Maximum tokens per response per tier — prevents runaway generation consuming quota.
TIER_MAX_TOKENS = {
    "fast": 300,
    "mid": 800,
    "strong": 2000
}

# Maximum characters allowed for an incoming prompt to prevent payload DoS.
MAX_PROMPT_CHARS = 500000

# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# PostgreSQL configuration
# BUG-12 FIX: Changed default fallback port from 5433 → 5432 to match docker-compose.yml.
# The docker-compose postgres service maps 5432:5432; the old 5433 default caused
# connection failures when DATABASE_URL was not explicitly set in the environment.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")

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

# Model registry — single source of truth for which models exist in each tier.
# Ordered by provider priority within each tier (NVIDIA > Groq > Cerebras > Google > Mistral > Cloudflare > Ollama).
# max_context is in tokens; use official spec where known, conservative placeholder otherwise.
# Vision models are excluded — they form a separate pool handled in a future phase.
MODEL_REGISTRY = {
    "strong": [
        # NVIDIA NIM strong
        {"provider": "nvidia", "model_id": "openai/gpt-oss-120b",                    "max_context": 128000},
        {"provider": "nvidia", "model_id": "abacusai/dracarys-llama-3.1-70b-instruct","max_context": 128000},
        {"provider": "nvidia", "model_id": "sarvamai/sarvam-m",                       "max_context": 32768},
        {"provider": "nvidia", "model_id": "meta/llama-3.1-70b-instruct",             "max_context": 128000},
        {"provider": "nvidia", "model_id": "nvidia/nemotron-3-ultra-550b-a55b",       "max_context": 128000},
        # Groq strong
        {"provider": "groq",   "model_id": "openai/gpt-oss-120b",                    "max_context": 128000},
        {"provider": "groq",   "model_id": "llama-3.3-70b-versatile",                "max_context": 128000},
        {"provider": "groq",   "model_id": "groq/compound",                          "max_context": 128000},
        # Cerebras strong
        {"provider": "cerebras","model_id": "zai-glm-4.7",                           "max_context": 128000},
        {"provider": "cerebras","model_id": "gpt-oss-120b",                          "max_context": 128000},
        # Google strong
        {"provider": "google", "model_id": "gemini-2.5-pro",                         "max_context": 2097152},
        {"provider": "google", "model_id": "gemini-3-pro-preview",                   "max_context": 1048576},
        {"provider": "google", "model_id": "gemini-pro-latest",                      "max_context": 1048576},
        # Mistral strong
        {"provider": "mistral","model_id": "mistral-large-latest",                   "max_context": 128000},
        {"provider": "mistral","model_id": "magistral-medium-latest",                "max_context": 256000},
        # Cloudflare strong
        {"provider": "cloudflare","model_id": "@cf/openai/gpt-oss-120b",             "max_context": 128000},
        {"provider": "cloudflare","model_id": "@cf/zai-org/glm-5.2",                 "max_context": 128000},
        {"provider": "cloudflare","model_id": "@cf/moonshotai/kimi-k2.6",            "max_context": 128000},
        # Ollama Cloud strong
        {"provider": "ollama", "model_id": "deepseek-v4-pro",                        "max_context": 128000},
        {"provider": "ollama", "model_id": "deepseek-v3.2",                          "max_context": 128000},
        {"provider": "ollama", "model_id": "mistral-large-3:675b",                   "max_context": 128000},
        {"provider": "ollama", "model_id": "glm-5.2",                                "max_context": 128000},
        {"provider": "ollama", "model_id": "kimi-k2.7-code",                         "max_context": 128000},
        {"provider": "ollama", "model_id": "nemotron-3-ultra",                       "max_context": 128000},
        {"provider": "ollama", "model_id": "qwen3.5:397b",                           "max_context": 32768},
        {"provider": "ollama", "model_id": "gpt-oss:120b",                           "max_context": 128000},
    ],
    "mid": [
        # NVIDIA NIM mid
        {"provider": "nvidia", "model_id": "openai/gpt-oss-20b",                     "max_context": 128000},
        {"provider": "nvidia", "model_id": "qwen/qwen3.5-122b-a10b",                 "max_context": 128000},
        {"provider": "nvidia", "model_id": "nvidia/nemotron-3-super-120b-a12b",      "max_context": 128000},
        {"provider": "nvidia", "model_id": "upstage/solar-10.7b-instruct",           "max_context": 4096},
        {"provider": "nvidia", "model_id": "nvidia/llama-3.3-nemotron-super-49b-v1", "max_context": 128000},
        {"provider": "nvidia", "model_id": "stockmark/stockmark-2-100b-instruct",    "max_context": 32768},
        {"provider": "nvidia", "model_id": "mistralai/mistral-large-3-675b-instruct-2512","max_context": 128000},
        {"provider": "nvidia", "model_id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning","max_context": 32768},
        {"provider": "nvidia", "model_id": "mistralai/mistral-medium-3.5-128b",      "max_context": 128000},
        # Groq mid
        {"provider": "groq",   "model_id": "meta-llama/llama-4-scout-17b-16e-instruct","max_context": 10485760},
        {"provider": "groq",   "model_id": "qwen/qwen3-32b",                         "max_context": 32768},
        {"provider": "groq",   "model_id": "qwen/qwen3.6-27b",                       "max_context": 32768},
        {"provider": "groq",   "model_id": "openai/gpt-oss-20b",                     "max_context": 128000},
        # Cerebras mid
        {"provider": "cerebras","model_id": "gemma-4-31b",                           "max_context": 131072},
        # Google mid
        {"provider": "google", "model_id": "gemini-2.5-flash",                       "max_context": 1048576},
        {"provider": "google", "model_id": "gemini-3-flash-preview",                 "max_context": 1048576},
        {"provider": "google", "model_id": "gemini-3.5-flash",                       "max_context": 1048576},
        {"provider": "google", "model_id": "gemini-flash-latest",                    "max_context": 1048576},
        # Mistral mid
        {"provider": "mistral","model_id": "mistral-medium-3.5",                     "max_context": 128000},
        {"provider": "mistral","model_id": "codestral-latest",                       "max_context": 32000},
        {"provider": "mistral","model_id": "devstral-latest",                        "max_context": 32000},
        {"provider": "mistral","model_id": "open-mistral-nemo",                      "max_context": 128000},
        # Cloudflare mid
        {"provider": "cloudflare","model_id": "@cf/meta/llama-3.3-70b-instruct-fp8-fast","max_context": 128000},
        {"provider": "cloudflare","model_id": "@cf/nvidia/nemotron-3-120b-a12b",     "max_context": 128000},
        {"provider": "cloudflare","model_id": "@cf/qwen/qwen2.5-coder-32b-instruct", "max_context": 32768},
        {"provider": "cloudflare","model_id": "@cf/qwen/qwq-32b",                    "max_context": 32768},
        {"provider": "cloudflare","model_id": "@cf/mistralai/mistral-small-3.1-24b-instruct","max_context": 32768},
        # Ollama Cloud mid
        {"provider": "ollama", "model_id": "nemotron-3-super",                       "max_context": 128000},
        {"provider": "ollama", "model_id": "gemma3:27b",                             "max_context": 131072},
    ],
    "fast": [
        # NVIDIA NIM fast
        {"provider": "nvidia", "model_id": "nvidia/nemotron-3-nano-30b-a3b",         "max_context": 32768},
        {"provider": "nvidia", "model_id": "google/gemma-2-2b-it",                   "max_context": 8192},
        {"provider": "nvidia", "model_id": "mistralai/mistral-small-4-119b-2603",    "max_context": 32768},
        {"provider": "nvidia", "model_id": "mistralai/ministral-14b-instruct-2512",  "max_context": 128000},
        {"provider": "nvidia", "model_id": "nvidia/nemotron-mini-4b-instruct",       "max_context": 4096},
        {"provider": "nvidia", "model_id": "meta/llama-3.1-8b-instruct",             "max_context": 128000},
        # Groq fast
        {"provider": "groq",   "model_id": "llama-3.1-8b-instant",                  "max_context": 128000},
        {"provider": "groq",   "model_id": "groq/compound-mini",                     "max_context": 128000},
        {"provider": "groq",   "model_id": "allam-2-7b",                             "max_context": 8192},
        # whisper-large-v3-turbo excluded per user note — not a general reasoning model
        # Google fast
        {"provider": "google", "model_id": "gemini-2.0-flash",                       "max_context": 1048576},
        {"provider": "google", "model_id": "gemini-2.5-flash-lite",                  "max_context": 1048576},
        {"provider": "google", "model_id": "gemini-3.1-flash-lite",                  "max_context": 1048576},
        # Mistral fast
        {"provider": "mistral","model_id": "mistral-small-latest",                   "max_context": 32000},
        {"provider": "mistral","model_id": "ministral-14b-latest",                   "max_context": 128000},
        {"provider": "mistral","model_id": "ministral-8b-latest",                    "max_context": 128000},
        {"provider": "mistral","model_id": "ministral-3b-latest",                    "max_context": 32000},
        # Cloudflare fast
        {"provider": "cloudflare","model_id": "@cf/openai/gpt-oss-20b",              "max_context": 128000},
        {"provider": "cloudflare","model_id": "@cf/meta/llama-3.1-8b-instruct-fp8",  "max_context": 128000},
        # Ollama Cloud fast
        {"provider": "ollama", "model_id": "gpt-oss:20b",                            "max_context": 128000},
    ],
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
