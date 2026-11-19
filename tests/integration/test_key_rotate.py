"""``key.rotate`` — build plan §13 M1 acceptance evidence: "key rotation/destruction ...
pass end to end." Covers both the ``content`` and ``profile_pack`` key purposes: rotation is
consequential (requires the same preview/challenge/confirm flow as ``erase``), mints a fresh
generation, and permanently crypto-shreds anything still referencing the destroyed generation.
"""

from __future__ import annotations

import re

import pytest

from operator_voice.application.service import ConfirmationRequired, OperatorVoiceService, ServiceError
from operator_voice.persistence import store


def _extract_literal(instructions: str) -> str:
    m = re.search(r"challenge_literal='([^']+)'", instructions)
    assert m, f"could not find a quoted challenge_literal in: {instructions!r}"
    return m.group(1)


@pytest.fixture
def service(migrated_db, in_memory_crypto):
    conn, _db_path = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


def test_key_rotate_requires_confirmation(service):
    service.setup()
    first = service.key_rotate(purpose="content")
    assert first["status"] == "confirmation_required"
    assert "permanently" in first["consequence"].lower() or "unreadable" in first["consequence"].lower()


def test_key_rotate_wrong_literal_is_rejected(service):
    service.setup()
    first = service.key_rotate(purpose="content")
    literal = _extract_literal(first["instructions"])
    with pytest.raises(ServiceError):
        service.key_rotate(purpose="content", challenge_literal="definitely-not-the-word")


def test_key_rotate_content_key_mints_new_generation_and_destroys_old_ciphertext(service):
    service.setup()

    # Establish the current content-key generation and remember it + the DEK it wraps.
    old_generation_id, old_dek = store.ensure_active_key_generation(service.conn, service.crypto, store.CONTENT_KEY_PURPOSE)

    first = service.key_rotate(purpose="content")
    literal = _extract_literal(first["instructions"])
    result = service.key_rotate(purpose="content", challenge_literal=literal)
    assert result["status"] == "rotated"
    assert result["purpose"] == "content"
    new_generation_id = result["new_generation_id"]
    assert new_generation_id != old_generation_id

    # The new generation is now the active one for future writes.
    active_id, active_dek = store.ensure_active_key_generation(service.conn, service.crypto, store.CONTENT_KEY_PURPOSE)
    assert active_id == new_generation_id
    assert active_dek != old_dek

    # The OLD generation is destroyed: fetching its DEK by id must now fail loudly (crypto-
    # shredding), never silently succeed or return the wrong key.
    with pytest.raises(Exception):
        store.dek_for_generation(service.conn, service.crypto, old_generation_id)


def test_key_rotate_profile_pack_key_is_independent_of_content_key(service):
    service.setup()
    content_id_before, _ = store.ensure_active_key_generation(service.conn, service.crypto, store.CONTENT_KEY_PURPOSE)

    first = service.key_rotate(purpose="profile_pack")
    literal = _extract_literal(first["instructions"])
    result = service.key_rotate(purpose="profile_pack", challenge_literal=literal)
    assert result["status"] == "rotated"
    assert result["purpose"] == "profile_pack"

    # Rotating the profile-pack key must never touch the independent content-key generation.
    content_id_after, _ = store.ensure_active_key_generation(service.conn, service.crypto, store.CONTENT_KEY_PURPOSE)
    assert content_id_after == content_id_before


def test_key_rotate_rejects_unknown_purpose(service):
    service.setup()
    with pytest.raises(ServiceError):
        service.key_rotate(purpose="not-a-real-purpose")
