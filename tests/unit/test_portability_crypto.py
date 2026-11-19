"""Passphrase-encrypted profile portability crypto — build plan v4 §13 M4 worklist 4 ("deterministic
encrypted export/import ... round-trip fidelity") + "signed manifests and version compatibility
checks". Adversarial coverage: wrong passphrase, ciphertext tamper, signed-manifest tamper, version
incompatibility, and confirmation that no plaintext leaks into the archive.
"""

from __future__ import annotations

import base64
import copy
import json

import pytest

from operator_voice.persistence import portability as pt

_PAYLOAD = {
    "subjects": [{"subject_id": "s1", "kind": "OPERATOR", "display_label": "me"}],
    "facts": [{"facet": "tone", "value": "direct", "state": "SUPPORTED"}],
    "secret_marker": "PLAINTEXT-MARKER-SHOULD-NEVER-APPEAR-IN-ARCHIVE",
}
_PASS = "correct horse battery staple"


def test_round_trip_fidelity():
    archive = pt.seal(_PASS, _PAYLOAD).to_dict()
    restored = pt.open_archive(_PASS, archive)
    assert restored == _PAYLOAD


def test_deterministic_given_same_salt():
    salt = b"\x01" * pt.SALT_LENGTH_BYTES
    a1 = pt.seal(_PASS, _PAYLOAD, salt=salt).to_dict()
    a2 = pt.seal(_PASS, _PAYLOAD, salt=salt).to_dict()
    assert a1["ciphertext"] == a2["ciphertext"]  # nonce-committing AEAD -> byte-identical


def test_random_salt_makes_two_exports_unlinkable():
    a1 = pt.seal(_PASS, _PAYLOAD).to_dict()
    a2 = pt.seal(_PASS, _PAYLOAD).to_dict()
    assert a1["kdf"]["salt"] != a2["kdf"]["salt"]
    assert a1["ciphertext"] != a2["ciphertext"]
    # both still open to the identical payload
    assert pt.open_archive(_PASS, a1) == pt.open_archive(_PASS, a2) == _PAYLOAD


def test_wrong_passphrase_fails_closed():
    archive = pt.seal(_PASS, _PAYLOAD).to_dict()
    with pytest.raises(pt.PortabilityError):
        pt.open_archive("wrong passphrase entirely", archive)


def test_ciphertext_tamper_is_detected():
    archive = pt.seal(_PASS, _PAYLOAD).to_dict()
    blob = bytearray(base64.b64decode(archive["ciphertext"]))
    blob[-1] ^= 0x01  # flip one bit of the GCM tag/ciphertext
    archive["ciphertext"] = base64.b64encode(bytes(blob)).decode("ascii")
    with pytest.raises(pt.PortabilityError):
        pt.open_archive(_PASS, archive)


def test_signed_manifest_tamper_is_detected():
    # The manifest {schema, version, kdf} is AAD-bound. Editing a KDF work factor in the header
    # (without knowing the passphrase) must fail the tag rather than silently changing how the
    # archive is opened.
    archive = pt.seal(_PASS, _PAYLOAD).to_dict()
    tampered = copy.deepcopy(archive)
    tampered["kdf"]["n"] = archive["kdf"]["n"] * 2
    with pytest.raises(pt.PortabilityError):
        pt.open_archive(_PASS, tampered)


def test_unsupported_schema_refused():
    archive = pt.seal(_PASS, _PAYLOAD).to_dict()
    archive["schema"] = "some-other-schema/v9"
    with pytest.raises(pt.PortabilityError):
        pt.open_archive(_PASS, archive)


def test_incompatible_major_version_refused():
    archive = pt.seal(_PASS, _PAYLOAD).to_dict()
    archive["version"] = "2.0"  # future breaking major -> refuse, do not guess
    with pytest.raises(pt.PortabilityError):
        pt.open_archive(_PASS, archive)


def test_archive_contains_no_plaintext_payload():
    archive = pt.seal(_PASS, _PAYLOAD).to_dict()
    blob = json.dumps(archive)
    assert "PLAINTEXT-MARKER-SHOULD-NEVER-APPEAR-IN-ARCHIVE" not in blob
    assert "direct" not in blob  # a fact value must not be readable in the archive
    # only the manifest header + opaque ciphertext are present
    assert set(archive.keys()) == {"schema", "version", "kdf", "ciphertext", "created_at"}


def test_empty_passphrase_refused():
    with pytest.raises(pt.PortabilityError):
        pt.seal("", _PAYLOAD)


def test_downgraded_scrypt_params_refused_even_if_self_consistent():
    """Defense in depth: an attacker crafts a *self-consistent* archive sealed with deliberately
    weak scrypt params (its own correct AAD + GCM tag for those params). Import must still refuse on
    the parameter floor — a KDF downgrade on an untrusted file cannot silently take effect."""
    import hashlib

    weak_kdf = {"algo": "scrypt", "n": 2, "r": 1, "p": 1, "dklen": pt.KEY_LENGTH_BYTES,
                "salt": base64.b64encode(b"\x02" * pt.SALT_LENGTH_BYTES).decode("ascii")}
    salt = base64.b64decode(weak_kdf["salt"])
    key = pt.derive_key(_PASS, salt, n=2, r=1, p=1)
    aad = pt._canonical_json(pt._manifest(weak_kdf))
    inner = {"schema": pt.PORTABLE_PROFILE_SCHEMA, "version": pt.PORTABLE_PROFILE_VERSION,
             "created_at": 0.0,
             "content_digest": hashlib.sha256(pt._canonical_json(_PAYLOAD)).hexdigest(),
             "payload": _PAYLOAD}
    blob = pt._seal_bytes(key, pt._canonical_json(inner), aad)
    archive = {"schema": pt.PORTABLE_PROFILE_SCHEMA, "version": pt.PORTABLE_PROFILE_VERSION,
               "kdf": weak_kdf, "ciphertext": base64.b64encode(blob).decode("ascii"), "created_at": 0.0}
    with pytest.raises(pt.PortabilityError):
        pt.open_archive(_PASS, archive)  # refused on the floor despite the correct passphrase


def test_inflated_scrypt_n_is_refused_before_key_derivation(monkeypatch):
    """An untrusted archive with a huge scrypt n would explode the import-time allocation. The
    parameter check must short-circuit BEFORE derive_key runs (proven by booby-trapping it)."""
    archive = pt.seal(_PASS, _PAYLOAD).to_dict()
    archive["kdf"]["n"] = 2 ** 40

    def _boom(*a, **k):
        raise AssertionError("derive_key must not run on an out-of-policy KDF")

    monkeypatch.setattr(pt, "derive_key", _boom)
    with pytest.raises(pt.PortabilityError):
        pt.open_archive(_PASS, archive)
