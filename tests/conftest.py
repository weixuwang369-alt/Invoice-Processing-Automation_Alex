import os

# A dummy but well-formed key so LLMClient() constructs successfully in tests
# (the openai SDK validates key *shape*, not validity, at construction time —
# no network call happens until complete_structured() is actually invoked).
# There is no offline/mock provider anymore; tests that need canned LLM
# output patch LLMClient.complete_structured directly — see the
# `patch_llm` fixture below.
os.environ.setdefault("LLM_PROVIDER", "xai")
os.environ.setdefault("XAI_API_KEY", "sk-test-dummy-key-not-real")

import sqlite3

import pytest

from setup_inventory_db import ALIAS_SEED, INVENTORY_SEED, SCHEMA
from src import key_store
from src.llm_client import LLMClient


@pytest.fixture
def temp_key_store(tmp_path, monkeypatch):
    """Points src.key_store at an isolated, per-test file so key/model tests
    never touch the real .secrets/local_config.json. Returns the path."""
    path = str(tmp_path / "local_config.json")
    monkeypatch.setattr(key_store, "STORE_PATH", path)
    return path


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "inventory.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO inventory (item, stock, unit_price, category) VALUES (?, ?, ?, ?)", INVENTORY_SEED
    )
    conn.executemany("INSERT INTO item_aliases (alias, canonical_item) VALUES (?, ?)", ALIAS_SEED)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def patch_llm(monkeypatch):
    """Stub LLMClient.complete_structured for tests that exercise LLM-calling
    agents. Pass a callable `responder(system, user, schema) -> BaseModel`;
    every complete_structured() call in the test is routed through it.
    """

    def _apply(responder):
        def fake_complete_structured(self, system, user, schema):
            return responder(system, user, schema)

        monkeypatch.setattr(LLMClient, "complete_structured", fake_complete_structured)

    return _apply
