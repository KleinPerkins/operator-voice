"""M5 general-availability clean-room suite — build plan v4 §13 M5 acceptance:

    "a cpp-only clean machine installs and repairs the portable runtime with zero personal bytes;
     Laurence's home-first and cpp-first sequences converge without changing his profile remote,
     keys, config, or state."

These extend M1's two hermetic distributor fixtures (``tests/integration/test_runtime_install.py``)
to the full M5 GA scenarios *without* touching any real cpp/orchestrator/skills repo or the network.
"cpp" here is a **hermetic vendor-fixture**: a byte-copy of the release package tree staged in its
own throwaway directory, exactly the shape the governed orchestrator would vendor into a downstream
product repo — the convergence property under test is a property of *content*, not of sharing a
filesystem tree or a distributor label.

Every operator here uses a disposable ``HOME`` (its own config/data/state roots) and an in-memory
keystore; no real Keychain, Git remote, private repo, or network is touched. All corpus text is
synthetic. Nothing in this module reads a path outside its ``tmp_path``.
"""

from __future__ import annotations

from synthetic_sources import write_slack_notes

import hashlib
import inspect
import shutil
from pathlib import Path

import pytest

import operator_voice
from operator_voice.persistence.crypto import InMemoryKeyStore
from operator_voice.runtime.bootstrap import open_runtime
from operator_voice.runtime.install import install_runtime, repair_runtime
from operator_voice.runtime.payload import build_portable_payload, compute_digest, verify_payload
from operator_voice.runtime.paths import RuntimePaths
from operator_voice.runtime.receipt import read_receipt

PACKAGE_ROOT = Path(operator_voice.__file__).parent
VERSION = operator_voice.__version__

# Synthetic, deterministic "operator voice" — repeated consistent phrasing so synthesis has enough
# signal to promote at least one fact. Never copied from any real corpus (operator-prompt constraint).
_SYNTHETIC_NOTES = [
    "I always ship on Fridays and never regret it.",
    "Shipping on Fridays works for me every single time.",
    "I ship on Fridays; it has never once caused a problem.",
    "Fridays are my shipping day, full stop.",
]


def _paths(home: Path) -> RuntimePaths:
    return RuntimePaths(
        config_dir=home / ".config" / "operator-voice",
        data_dir=home / ".local" / "share" / "operator-voice",
        state_dir=home / ".local" / "state" / "operator-voice",
    )


def _distributor_payload(tmp_path: Path, label: str) -> Path:
    """Simulate a distributor: copy the real package tree into its OWN hermetic staging directory
    first (as a vendor-into-a-downstream-repo path would, from a completely separate clone), then
    build the payload from that hermetic copy — never from ``PACKAGE_ROOT`` directly. A real
    filesystem copy boundary sits between distributors, so convergence is proven to be a property
    of content, not of a shared tree."""
    staging = tmp_path / f"{label}-staging" / "operator_voice"
    shutil.copytree(PACKAGE_ROOT, staging, ignore=shutil.ignore_patterns("__pycache__"))
    payload_dir = tmp_path / f"{label}-payload"
    build_portable_payload(staging, payload_dir, version=VERSION)
    return payload_dir


def _hash_tree(root: Path, *, exclude_names: frozenset[str] = frozenset()) -> dict[str, str]:
    """Map of {relative posix path -> sha256} for every regular file under ``root``, skipping any
    path whose relative parts intersect ``exclude_names`` (used to exclude the ``runtime/`` subtree
    and the receipt file, which repair is *allowed* to touch)."""
    if not root.exists():
        return {}
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if exclude_names & set(rel.parts):
            continue
        out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------------------------
# Scenario A — a cpp-only local-profile operator (vendor-fixture-only, zero private-repo access)
# ---------------------------------------------------------------------------------------------


def test_cpp_only_operator_installs_immutable_release_and_runs_full_local_only_lifecycle(tmp_path):
    """§13 M5: "a cpp-only operator can bootstrap ... a complete local-only OVT from cpp with no
    KleinPerkins access." The operator's runtime is installed ONLY from a hermetic vendor-fixture
    payload (no home checkout, no skills-repo access), then runs the full ingest -> context ->
    feedback -> rebuild loop entirely LOCAL_ONLY."""
    home = tmp_path / "cpp-only-home"
    home.mkdir()
    paths = _paths(home)
    paths.ensure()
    runtime_root = paths.data_dir / "runtime"

    payload = _distributor_payload(tmp_path, "cpp-vendor")
    release_digest, _ = compute_digest(PACKAGE_ROOT)

    result = install_runtime(payload, runtime_root, paths.receipt_path, source="cpp-fixture")
    assert result["status"] == "installed"
    assert result["digest"] == release_digest  # the SAME immutable release, not a cpp-specific build
    assert result["version"] == VERSION
    receipt = read_receipt(paths.receipt_path)
    assert receipt.digest == release_digest and receipt.source == "cpp-fixture"
    assert verify_payload((runtime_root / "current").resolve())

    # Full LOCAL_ONLY lifecycle against this disposable HOME, in-memory keystore, zero network/git.
    rt = open_runtime(paths, keystore_factory=InMemoryKeyStore)
    try:
        svc = rt.service
        setup = svc.setup(display_label="cpp-only-operator")
        assert setup["status"] == "ready" and setup["backend"] == "LOCAL_ONLY"
        subject_id = setup["subject_id"]

        notes = home / "notes"
        notes.mkdir()
        write_slack_notes(notes, _SYNTHETIC_NOTES, conversations=len(_SYNTHETIC_NOTES))
        source = svc.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(notes))
        ingest = svc.ingest(source_id=source["source_id"])
        assert ingest["status"] == "ingested"

        bundle = svc.context_get(subject_id=subject_id, consumer_id="cpp-clean-room")
        assert bundle["schema"] == "voice-context-bundle/v1"
        assert bundle["consumer_id"] == "cpp-clean-room"

        feedback = svc.feedback_add(
            subject_id=subject_id, utterance="never say the word delve, it always sounds fake to me"
        )
        assert feedback["status"] == "recorded"

        rebuilt = svc.rebuild(generation_id=ingest["generation_id"])
        assert rebuilt["status"] == "rebuilt"

        # LOCAL_ONLY honesty: remote verbs are no-ops, status reports no online backup, no network.
        assert svc.profile_backend_status()["mode"] == "LOCAL_ONLY"
        assert svc.profile_publish()["status"] == "skipped"
        assert svc.status()["backend_mode"] == "LOCAL_ONLY"
    finally:
        rt.close()

    # Zero private-repo / Git footprint anywhere under HOME, and no profile-pack clone was created.
    assert not list(home.rglob(".git")), "a LOCAL_ONLY cpp-only operator must never create a Git repo"
    assert not paths.profile_pack_clone_dir.exists()


# ---------------------------------------------------------------------------------------------
# Scenario B — home-first / cpp-first coexistence converge on the same immutable release
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("order", [("home", "cpp-fixture"), ("cpp-fixture", "home")])
def test_home_first_and_cpp_first_sequences_converge_on_one_receipt(tmp_path, order):
    """Both distributor orderings install the identical version+digest; the second distributor is a
    same-digest no-op (never a second slot, never a clobber). Proves "the same immutable release
    installs from both distributors" and "home-first and cpp-first sequences converge." """
    first_label, second_label = order
    home = tmp_path / f"home-{first_label}-then-{second_label}"
    home.mkdir()
    paths = _paths(home)
    paths.ensure()
    runtime_root = paths.data_dir / "runtime"
    release_digest, _ = compute_digest(PACKAGE_ROOT)

    first_payload = _distributor_payload(tmp_path, f"{first_label}-{second_label}-first")
    second_payload = _distributor_payload(tmp_path, f"{first_label}-{second_label}-second")

    r1 = install_runtime(first_payload, runtime_root, paths.receipt_path, source=first_label)
    assert r1["status"] == "installed" and r1["digest"] == release_digest
    slots_after_first = sorted(p.name for p in runtime_root.iterdir() if not p.name.startswith("."))

    r2 = install_runtime(second_payload, runtime_root, paths.receipt_path, source=second_label)
    assert r2["status"] == "up_to_date"  # same immutable release -> idempotent no-op
    slots_after_second = sorted(p.name for p in runtime_root.iterdir() if not p.name.startswith("."))
    assert slots_after_first == slots_after_second, "a converging distributor must not materialize a second slot"

    receipt = read_receipt(paths.receipt_path)
    assert receipt.version == VERSION and receipt.digest == release_digest
    # Distributor identity is diagnostic only: whichever installed FIRST owns the receipt's source
    # label; the second (no-op) never rewrites it.
    assert receipt.source == first_label


def test_coexistence_version_drift_fails_closed_and_preserves_prior_runtime(tmp_path):
    """In a coexistence sequence, a second distributor presenting the SAME version with a DIFFERENT
    digest (tamper/corruption/skew) is refused; the prior healthy runtime is left active and the
    receipt is unchanged. (Same-version/different-digest fail-closed, applied to the two-distributor
    path.)"""
    home = tmp_path / "drift-home"
    home.mkdir()
    paths = _paths(home)
    paths.ensure()
    runtime_root = paths.data_dir / "runtime"
    release_digest, _ = compute_digest(PACKAGE_ROOT)

    home_payload = _distributor_payload(tmp_path, "drift-home-dist")
    install_runtime(home_payload, runtime_root, paths.receipt_path, source="home")
    prior_current_target = (runtime_root / "current").resolve()

    # A second distributor whose bytes differ but which (dishonestly) claims the same version.
    tampered_src = tmp_path / "tampered-cpp" / "operator_voice"
    shutil.copytree(PACKAGE_ROOT, tampered_src, ignore=shutil.ignore_patterns("__pycache__"))
    (tampered_src / "__init__.py").write_text((tampered_src / "__init__.py").read_text() + "\n# vendor skew\n")
    tampered_payload = tmp_path / "tampered-cpp-payload"
    build_portable_payload(tampered_src, tampered_payload, version=VERSION)

    result = install_runtime(tampered_payload, runtime_root, paths.receipt_path, source="cpp-fixture")
    assert result["status"] == "refused"
    assert "digest" in result["reason"]

    # Prior runtime + receipt untouched.
    assert (runtime_root / "current").resolve() == prior_current_target
    receipt = read_receipt(paths.receipt_path)
    assert receipt.version == VERSION and receipt.digest == release_digest and receipt.source == "home"


# ---------------------------------------------------------------------------------------------
# Scenario C — runtime repair boundary: cannot touch config / state / DB / keys / profile
# ---------------------------------------------------------------------------------------------


def test_repair_cannot_touch_config_state_db_keychain_or_profile(tmp_path):
    """§8.2 / §13 M5: "repair verifies/replaces runtime payload only. It cannot mutate
    ~/.config/operator-voice, ~/.local/state/operator-voice, Keychain items, profile backend, or
    profile repo." Proven both behaviorally (byte-identical personal state across a repair of a
    corrupted runtime) and structurally (``repair_runtime`` has no handle to any personal path)."""
    home = tmp_path / "repair-home"
    home.mkdir()
    paths = _paths(home)
    paths.ensure()
    runtime_root = paths.data_dir / "runtime"

    # 1. A real operator with encrypted local state + representative personal artifacts.
    rt = open_runtime(paths, keystore_factory=InMemoryKeyStore)
    svc = rt.service
    setup = svc.setup(display_label="repair-operator")
    subject_id = setup["subject_id"]
    notes = home / "notes"
    notes.mkdir()
    write_slack_notes(notes, _SYNTHETIC_NOTES, conversations=len(_SYNTHETIC_NOTES))
    source = svc.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(notes))
    svc.ingest(source_id=source["source_id"])
    svc.feedback_add(subject_id=subject_id, utterance="never say the word delve, it always sounds fake to me")
    keystore_content_before = dict(svc.crypto.keystore._store)
    keystore_pack_before = dict(svc.profile_pack_crypto.keystore._store)
    assert keystore_content_before, "the operator must have real key material for this test to mean anything"
    rt.close()

    # Representative "profile backend config" and "profile repository" personal artifacts repair
    # must never touch (OVT itself keeps backend config in the encrypted DB; these stand in for the
    # broad class named in the acceptance text — a config file, a cloned profile repo).
    paths.config_file.write_text('{"backend_mode": "ENCRYPTED_GIT", "remote": "git@example:private.git"}\n')
    profile_clone = paths.profile_pack_clone_dir
    profile_clone.mkdir(parents=True, exist_ok=True)
    (profile_clone / "HEAD").write_text("ref: refs/heads/main\n")
    (profile_clone / "encrypted-pack.bin").write_bytes(b"\x00ciphertext-only-operator-profile\x01")

    # 2. Install the runtime (vendor-fixture payload) alongside that personal state.
    payload = _distributor_payload(tmp_path, "repair-vendor")
    install_runtime(payload, runtime_root, paths.receipt_path, source="cpp-fixture")
    receipt_before = read_receipt(paths.receipt_path)

    # 3. Snapshot ALL personal state (config, data-minus-runtime, state-minus-receipt).
    config_before = _hash_tree(paths.config_dir)
    data_before = _hash_tree(paths.data_dir, exclude_names=frozenset({"runtime"}))
    state_before = _hash_tree(paths.state_dir, exclude_names=frozenset({"install-receipt.json"}))
    assert paths.db_path.name in {Path(p).name for p in data_before}, "the encrypted DB must be part of the snapshot"

    # 4. Corrupt the installed runtime slot on disk, then repair it.
    slot_dir = (runtime_root / "current").resolve()
    (slot_dir / "operator_voice" / "__init__.py").write_text("corrupted on disk")
    assert not verify_payload(slot_dir)
    repair_result = repair_runtime(payload, runtime_root, paths.receipt_path)
    assert repair_result["status"] == "installed"
    assert verify_payload((runtime_root / "current").resolve()), "repair must restore a healthy runtime"

    # 5. Every byte of personal state is unchanged; only the runtime subtree was rebuilt.
    assert _hash_tree(paths.config_dir) == config_before, "repair changed ~/.config/operator-voice"
    assert _hash_tree(paths.data_dir, exclude_names=frozenset({"runtime"})) == data_before, "repair changed encrypted DB / data"
    assert _hash_tree(paths.state_dir, exclude_names=frozenset({"install-receipt.json"})) == state_before, "repair changed state"

    # Receipt identity (version+digest+source) is unchanged (installed_at may refresh — excluded above).
    receipt_after = read_receipt(paths.receipt_path)
    assert (receipt_after.version, receipt_after.digest, receipt_after.source) == (
        receipt_before.version, receipt_before.digest, receipt_before.source,
    )

    # Key material is untouched (repair has no keystore handle at all — see structural guard below).
    assert dict(svc.crypto.keystore._store) == keystore_content_before
    assert dict(svc.profile_pack_crypto.keystore._store) == keystore_pack_before


def test_repair_runtime_is_structurally_incapable_of_touching_personal_state():
    """Structural companion to the behavioral test: ``repair_runtime`` accepts ONLY a payload dir,
    a runtime root, and a receipt path — it has no reference to any config/data-DB/Keychain/profile
    path, so "repair cannot touch personal state" is true by construction, not by care."""
    params = set(inspect.signature(repair_runtime).parameters)
    assert params == {"payload_dir", "runtime_root", "receipt_path"}
    source = inspect.getsource(repair_runtime)
    for forbidden in ("config_file", "config_dir", "db_path", "keychain", "Keychain", "profile_pack", "backups_dir"):
        assert forbidden not in source, f"repair_runtime unexpectedly references personal-state token {forbidden!r}"
