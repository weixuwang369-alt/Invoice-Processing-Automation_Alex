from src import key_store
from src.config import PROVIDER_DEFAULTS, load_settings


def test_model_defaults_to_provider_default(temp_key_store, monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = load_settings()
    assert settings.llm_model == PROVIDER_DEFAULTS[settings.llm_provider]["model"]


def test_model_env_var_overrides_provider_default(temp_key_store, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "grok-4")
    settings = load_settings()
    assert settings.llm_model == "grok-4"


def test_stored_model_overrides_env_var(temp_key_store, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "xai")
    monkeypatch.setenv("LLM_MODEL", "grok-4")
    key_store.save("xai", "sk-test-key")
    key_store.save_model("xai", "grok-3-mini")
    settings = load_settings()
    assert settings.llm_model == "grok-3-mini"
