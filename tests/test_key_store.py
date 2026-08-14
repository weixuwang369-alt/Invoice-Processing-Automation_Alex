from src import key_store


def test_no_store_file_returns_empty_status(temp_key_store):
    status = key_store.status()
    assert status["active_provider"] is None
    assert all(not p["has_key"] for p in status["providers"].values())


def test_save_and_retrieve_key(temp_key_store):
    key_store.save("xai", "sk-abcdef1234")
    assert key_store.get_active_provider() == "xai"
    assert key_store.get_key("xai") == "sk-abcdef1234"


def test_masked_never_exposes_full_key(temp_key_store):
    key_store.save("openai", "sk-supersecretvalue")
    masked = key_store.masked("openai")
    assert "supersecretvalue" not in masked
    assert masked.endswith("alue")


def test_clear_key_removes_it_but_keeps_provider_selection(temp_key_store):
    key_store.save("xai", "sk-abcdef1234")
    key_store.clear_key("xai")
    assert key_store.get_key("xai") is None
    assert key_store.get_active_provider() == "xai"  # provider choice persists, only the secret is gone


def test_store_file_is_owner_only_permissions(temp_key_store):
    import os
    import stat

    key_store.save("xai", "sk-abcdef1234")
    mode = stat.S_IMODE(os.stat(temp_key_store).st_mode)
    assert mode == 0o600


def test_masked_length_is_constant_regardless_of_key_length(temp_key_store):
    key_store.save("openai", "sk-" + "a" * 17)  # 20 chars
    short_masked = key_store.masked("openai")
    key_store.save("anthropic", "sk-ant-api03-" + "b" * 107)  # 120 chars
    long_masked = key_store.masked("anthropic")
    assert len(short_masked) == len(long_masked)


def test_save_model_and_get_model(temp_key_store):
    key_store.save_model("anthropic", "claude-opus-5")
    assert key_store.get_model("anthropic") == "claude-opus-5"
    assert key_store.status()["providers"]["anthropic"]["model"] == "claude-opus-5"


def test_save_model_with_blank_value_does_not_clobber_stored_model(temp_key_store):
    key_store.save_model("anthropic", "claude-opus-5")
    key_store.save_model("anthropic", None)
    assert key_store.get_model("anthropic") == "claude-opus-5"


def test_batch_notify_mode_defaults_to_on_complete(temp_key_store):
    assert key_store.get_batch_notify_mode() == "on_complete"


def test_save_and_retrieve_batch_notify_mode(temp_key_store):
    key_store.save_batch_notify_mode("per_invoice")
    assert key_store.get_batch_notify_mode() == "per_invoice"


def test_save_batch_notify_mode_with_blank_value_does_not_clobber(temp_key_store):
    key_store.save_batch_notify_mode("per_invoice")
    key_store.save_batch_notify_mode(None)
    assert key_store.get_batch_notify_mode() == "per_invoice"
