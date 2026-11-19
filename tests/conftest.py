"""Shared fixtures. Every test in this suite uses synthetic/adversarial fixture data only —
never live operator transcripts, communications, credentials, or external-person data (operator
prompt constraint). Keychain tests use a disposable, throwaway keychain file destroyed at
teardown; they never touch the operator's real login Keychain.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from operator_voice.persistence.crypto import (
    CryptoContext,
    InMemoryKeyStore,
    MacKeychainKeyStore,
    destroy_test_keychain,
    disposable_test_keychain,
)
from operator_voice.persistence import migrations
from operator_voice.persistence.sqlite import connect


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def in_memory_crypto() -> CryptoContext:
    """Fast crypto-correctness fixture — does not exercise the real Keychain. See
    ``disposable_keychain_crypto`` for the real-Keychain integration path."""
    return CryptoContext(InMemoryKeyStore())


@pytest.fixture
def disposable_keychain_crypto():
    tmp_dir = tempfile.mkdtemp(prefix="ovt-test-keychain-")
    keychain_path = disposable_test_keychain(tmp_dir)
    ctx = CryptoContext(MacKeychainKeyStore(service="operator-voice-test", keychain_path=keychain_path))
    try:
        yield ctx
    finally:
        destroy_test_keychain(keychain_path)
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def migrated_db(tmp_path: Path):
    db_path = tmp_path / "operator-voice.sqlite3"
    conn = connect(db_path)
    migrations.migrate(conn)
    try:
        yield conn, db_path
    finally:
        conn.close()
