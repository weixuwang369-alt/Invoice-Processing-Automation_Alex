"""Configuration, recomputed fresh on every call rather than cached at import
time. That's what lets a long-running server process (the FastAPI backend)
pick up a provider/API key saved through the settings UI without a restart —
see src/key_store.py. The CLI is a fresh process per invocation either way,
so this costs it nothing.

Precedence for the API key: local key store (set via the UI) > environment
variable (.env / shell) > unset. Provider precedence: local key store >
LLM_PROVIDER env var > "xai" (the assignment's named engine).

There is no offline/mock mode. If no key is configured, LLMClient raises a
clear error the moment something actually needs to call the LLM — see
src/llm_client.py. JSON/CSV/XML invoices still parse and validate with zero
configuration, since that path never touches an LLM; approval and free-text
ingestion do.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from src import key_store

# xai   -> Grok via the OpenAI-compatible endpoint at https://api.x.ai/v1 (assignment default)
# openai / anthropic -> drop-in alternatives per the assignment's "other models are acceptable" allowance
PROVIDER_DEFAULTS = {
    "xai": {"base_url": "https://api.x.ai/v1", "model": "grok-3", "env_key": "XAI_API_KEY"},
    "openai": {"base_url": None, "model": "gpt-4o-mini", "env_key": "OPENAI_API_KEY"},
    "anthropic": {"base_url": None, "model": "claude-sonnet-5", "env_key": "ANTHROPIC_API_KEY"},
}
DEFAULT_PROVIDER = "xai"

# Models offered in the Settings dropdown per provider. The first entry is
# each provider's default. This list is intentionally short — an unlisted
# model can still be set via the LLM_MODEL environment variable.
PROVIDER_MODELS = {
    "xai": ["grok-3", "grok-3-mini", "grok-4"],
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
    "anthropic": ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001", "claude-fable-5"],
}


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str
    llm_base_url: str | None
    llm_api_key: str | None

    inventory_db_path: str
    log_dir: str

    approval_scrutiny_threshold: float


def load_settings() -> Settings:
    provider = key_store.get_active_provider() or os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER).lower()
    if provider not in PROVIDER_DEFAULTS:
        provider = DEFAULT_PROVIDER
    defaults = PROVIDER_DEFAULTS[provider]

    api_key = key_store.get_key(provider) or os.getenv(defaults["env_key"])
    model = key_store.get_model(provider) or os.getenv("LLM_MODEL", defaults["model"])

    return Settings(
        llm_provider=provider,
        llm_model=model,
        llm_base_url=os.getenv("LLM_BASE_URL", defaults["base_url"]),
        llm_api_key=api_key,
        inventory_db_path=os.getenv("INVENTORY_DB_PATH", "inventory.db"),
        log_dir=os.getenv("LOG_DIR", "logs"),
        approval_scrutiny_threshold=float(os.getenv("APPROVAL_SCRUTINY_THRESHOLD", "10000")),
    )
