"""Atomic runtime install/repair + portable payload — build plan v3 §13 M1 acceptance: "Home-first
and cpp-fixture-first installs converge to the same receipt; same digest is a no-op; same-version/
different-digest refuses for distributor payloads but upgrades from a home checkout or with
``--force``; interruption preserves the prior runtime; repair changes runtime only; every state
renders one plain next step." Also §6.1: "two hermetic distributor fixtures must prove
home/cpp-future convergence without cpp mutation."
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import operator_voice
from operator_voice.runtime.install import install_runtime, repair_runtime
from operator_voice.runtime.payload import (
    PayloadIntegrityError,
    build_portable_payload,
    compute_digest,
    load_manifest,
    verify_payload,
)
from operator_voice.runtime.receipt import read_receipt

PACKAGE_ROOT = Path(operator_voice.__file__).parent


# ---------------------------------------------------------------------------------------------
# Payload determinism / two hermetic distributor fixtures
# ---------------------------------------------------------------------------------------------


def test_payload_build_excludes_pycache_and_is_reproducible(tmp_path: Path):
    out_a = tmp_path / "payload-a"
    out_b = tmp_path / "payload-b"
    m1 = build_portable_payload(PACKAGE_ROOT, out_a, version="0.1.0")
    m2 = build_portable_payload(PACKAGE_ROOT, out_b, version="0.1.0")
    assert m1.digest == m2.digest
    assert m1.file_count == m2.file_count
    assert not any("__pycache__" in f for f in m1.files)


def test_portable_payload_excludes_downstream_repository_references(tmp_path: Path):
    payload = tmp_path / "payload"
    manifest = build_portable_payload(PACKAGE_ROOT, payload, version="0.1.0")
    forbidden = re.compile(r"card-platform-product|cpp-stuff|affirm/(src|vendor)|(^|[^A-Za-z.])cpp([^A-Za-z]|$)")
    violations = [path for path in manifest.files if forbidden.search((payload / path).read_text())]
    assert violations == []


def test_payload_build_writes_a_generic_tool_runtime_contract(tmp_path: Path):
    """Build plan v4 §13 step 2.3: a downstream ``kind: tool`` reconciler (cpp's, authored under
    ITS OWN governance) provisions a runnable CLI from ``tool-runtime.json`` alone, never by
    importing this package. The contract must describe a module that is actually importable/
    ``python -m``-runnable from the payload root and a requirements file that actually ships."""
    out = tmp_path / "payload"
    build_portable_payload(PACKAGE_ROOT, out, version="0.1.0")
    contract = json.loads((out / "tool-runtime.json").read_text(encoding="utf-8"))
    assert contract["schema"] == "tool-runtime-contract/v1"
    assert contract["runtime"] == "python"
    assert contract["package"] == "operator_voice"
    assert contract["entrypoint_module"] == "operator_voice.interfaces.cli"
    assert contract["requirements_file"] == "operator_voice/runtime/requirements.json"
    assert (out / contract["requirements_file"]).is_file()
    entry_path = (out / Path(*contract["entrypoint_module"].split("."))).with_suffix(".py")
    assert entry_path.is_file()


def test_tool_runtime_contract_lives_outside_the_hashed_digest(tmp_path: Path):
    """tool-runtime.json is metadata ABOUT the payload, produced alongside payload-manifest.json —
    tampering with it must never change the digest computed over the package tree."""
    out = tmp_path / "payload"
    manifest = build_portable_payload(PACKAGE_ROOT, out, version="0.1.0")
    (out / "tool-runtime.json").write_text('{"tampered": true}\n', encoding="utf-8")
    digest_after, _ = compute_digest(out / "operator_voice")
    assert digest_after == manifest.digest


def test_payload_refuses_on_forbidden_file_type(tmp_path: Path):
    poisoned_root = tmp_path / "poisoned" / "operator_voice"
    shutil.copytree(PACKAGE_ROOT, poisoned_root)
    (poisoned_root / "oops.sqlite3").write_bytes(b"not actually a database, just a probe")
    with pytest.raises(PayloadIntegrityError):
        build_portable_payload(poisoned_root, tmp_path / "out", version="0.1.0")


def _distributor_fixture(tmp_path: Path, label: str) -> Path:
    """Simulates a distributor: copies the real package tree into its OWN hermetic staging
    directory first (as a future cpp-vendoring path would do from a completely separate clone),
    then builds the payload from that hermetic copy — never from ``PACKAGE_ROOT`` directly for
    the second fixture, so a real filesystem copy boundary sits between "home" and "cpp-future"."""
    staging = tmp_path / f"{label}-staging" / "operator_voice"
    shutil.copytree(PACKAGE_ROOT, staging, ignore=shutil.ignore_patterns("__pycache__"))
    payload_dir = tmp_path / f"{label}-payload"
    build_portable_payload(staging, payload_dir, version="0.1.0")
    return payload_dir


def test_home_and_cpp_future_distributor_fixtures_converge_to_the_same_receipt(tmp_path: Path):
    home_payload = _distributor_fixture(tmp_path, "home")
    cpp_future_payload = _distributor_fixture(tmp_path, "cpp-future")

    home_root, home_receipt = tmp_path / "home-runtime", tmp_path / "home-receipt.json"
    cpp_root, cpp_receipt = tmp_path / "cpp-runtime", tmp_path / "cpp-receipt.json"

    home_result = install_runtime(home_payload, home_root, home_receipt, source="home")
    cpp_result = install_runtime(cpp_future_payload, cpp_root, cpp_receipt, source="cpp-fixture")

    assert home_result["status"] == cpp_result["status"] == "installed"
    assert home_result["digest"] == cpp_result["digest"]
    assert home_result["version"] == cpp_result["version"]

    home_receipt_obj = read_receipt(home_receipt)
    cpp_receipt_obj = read_receipt(cpp_receipt)
    assert home_receipt_obj.digest == cpp_receipt_obj.digest
    assert home_receipt_obj.version == cpp_receipt_obj.version
    assert home_receipt_obj.source == "home"
    assert cpp_receipt_obj.source == "cpp-fixture"  # distributor identity differs; release identity does not


# ---------------------------------------------------------------------------------------------
# Install state machine
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def payload(tmp_path: Path) -> Path:
    out = tmp_path / "payload"
    build_portable_payload(PACKAGE_ROOT, out, version="1.0.0")
    return out


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


@pytest.fixture
def receipt_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "install-receipt.json"


def test_fresh_install_activates_current_symlink(payload, runtime_root, receipt_path):
    result = install_runtime(payload, runtime_root, receipt_path, source="home")
    assert result["status"] == "installed"
    current = runtime_root / "current"
    assert current.is_symlink()
    assert (current / "operator_voice" / "__init__.py").exists()
    receipt = read_receipt(receipt_path)
    assert receipt.version == "1.0.0"


def test_same_digest_reinstall_is_a_noop(payload, runtime_root, receipt_path):
    install_runtime(payload, runtime_root, receipt_path, source="home")
    slots_before = sorted(p.name for p in runtime_root.iterdir() if not p.name.startswith("."))
    result = install_runtime(payload, runtime_root, receipt_path, source="home")
    assert result["status"] == "up_to_date"
    slots_after = sorted(p.name for p in runtime_root.iterdir() if not p.name.startswith("."))
    assert slots_before == slots_after  # no new slot materialized


def test_same_version_different_digest_refuses_for_distributor_payloads(tmp_path, payload, runtime_root, receipt_path):
    install_runtime(payload, runtime_root, receipt_path, source="home")
    tampered_source = tmp_path / "tampered-src" / "operator_voice"
    shutil.copytree(PACKAGE_ROOT, tampered_source, ignore=shutil.ignore_patterns("__pycache__"))
    (tampered_source / "__init__.py").write_text((tampered_source / "__init__.py").read_text() + "\n# tampered\n")
    tampered_payload = tmp_path / "tampered-payload"
    build_portable_payload(tampered_source, tampered_payload, version="1.0.0")

    result = install_runtime(tampered_payload, runtime_root, receipt_path, source="cpp-fixture")
    assert result["status"] == "refused"
    assert "digest" in result["reason"]
    assert "--force" in result["next_step"]
    # the previously-installed, healthy slot must still be the active one
    receipt = read_receipt(receipt_path)
    assert receipt.version == "1.0.0"
    assert receipt.digest == load_manifest(payload).digest


def test_same_version_different_digest_home_install_upgrades(tmp_path, payload, runtime_root, receipt_path):
    install_runtime(payload, runtime_root, receipt_path, source="home")
    prior_receipt = read_receipt(receipt_path)
    updated_source = tmp_path / "updated-src" / "operator_voice"
    shutil.copytree(PACKAGE_ROOT, updated_source, ignore=shutil.ignore_patterns("__pycache__"))
    (updated_source / "__init__.py").write_text((updated_source / "__init__.py").read_text() + "\n# post-release fix\n")
    updated_payload = tmp_path / "updated-payload"
    build_portable_payload(updated_source, updated_payload, version="1.0.0")

    result = install_runtime(updated_payload, runtime_root, receipt_path, source="home")
    assert result["status"] == "installed"
    receipt = read_receipt(receipt_path)
    assert receipt.version == "1.0.0"
    assert receipt.digest != prior_receipt.digest
    assert receipt.digest == load_manifest(updated_payload).digest
    assert verify_payload((runtime_root / "current").resolve())


def test_same_version_different_digest_force_upgrades(tmp_path, payload, runtime_root, receipt_path):
    install_runtime(payload, runtime_root, receipt_path, source="home")
    tampered_source = tmp_path / "tampered-src" / "operator_voice"
    shutil.copytree(PACKAGE_ROOT, tampered_source, ignore=shutil.ignore_patterns("__pycache__"))
    (tampered_source / "__init__.py").write_text((tampered_source / "__init__.py").read_text() + "\n# forced refresh\n")
    tampered_payload = tmp_path / "tampered-payload"
    build_portable_payload(tampered_source, tampered_payload, version="1.0.0")

    result = install_runtime(tampered_payload, runtime_root, receipt_path, source="cpp-fixture", force=True)
    assert result["status"] == "installed"
    receipt = read_receipt(receipt_path)
    assert receipt.digest == load_manifest(tampered_payload).digest


def test_healthy_newer_version_is_not_downgraded(tmp_path, runtime_root, receipt_path):
    v2 = tmp_path / "v2-payload"
    build_portable_payload(PACKAGE_ROOT, v2, version="2.0.0")
    install_runtime(v2, runtime_root, receipt_path, source="home")

    v1 = tmp_path / "v1-src" / "operator_voice"
    shutil.copytree(PACKAGE_ROOT, v1, ignore=shutil.ignore_patterns("__pycache__"))
    (v1 / "__init__.py").write_text((v1 / "__init__.py").read_text() + "\n# older release content\n")
    v1_payload = tmp_path / "v1-payload"
    build_portable_payload(v1, v1_payload, version="1.0.0")

    result = install_runtime(v1_payload, runtime_root, receipt_path, source="home")
    assert result["status"] == "refused"
    assert "downgrade" in result["reason"]
    receipt = read_receipt(receipt_path)
    assert receipt.version == "2.0.0"


def test_interruption_before_activation_preserves_the_prior_runtime(tmp_path, payload, runtime_root, receipt_path, monkeypatch):
    install_runtime(payload, runtime_root, receipt_path, source="home")
    prior_current_target = (runtime_root / "current").resolve()

    v2 = tmp_path / "v2-payload"
    build_portable_payload(PACKAGE_ROOT, v2, version="2.0.0")

    import operator_voice.runtime.install as install_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated interruption before activation")

    monkeypatch.setattr(install_mod, "_activate_slot", _boom)
    with pytest.raises(RuntimeError):
        install_runtime(v2, runtime_root, receipt_path, source="home")

    # `current` must be untouched — the interrupted install never got to the atomic swap
    assert (runtime_root / "current").resolve() == prior_current_target
    receipt = read_receipt(receipt_path)
    assert receipt.version == "1.0.0"


def test_repair_is_a_noop_on_a_healthy_runtime(payload, runtime_root, receipt_path):
    install_runtime(payload, runtime_root, receipt_path, source="home")
    result = repair_runtime(payload, runtime_root, receipt_path)
    assert result["status"] == "ok"


def test_repair_rebuilds_a_corrupted_slot_without_touching_the_receipt(payload, runtime_root, receipt_path):
    install_runtime(payload, runtime_root, receipt_path, source="home")
    receipt_before = read_receipt(receipt_path)

    slot_dir = (runtime_root / "current").resolve()
    victim = slot_dir / "operator_voice" / "__init__.py"
    victim.write_text("corrupted on disk")

    assert not verify_payload(slot_dir)
    result = repair_runtime(payload, runtime_root, receipt_path)
    assert result["status"] == "installed"
    assert verify_payload((runtime_root / "current").resolve())
    receipt_after = read_receipt(receipt_path)
    assert receipt_after.version == receipt_before.version
    assert receipt_after.digest == receipt_before.digest


def test_repair_refuses_when_payload_does_not_match_receipt(tmp_path, payload, runtime_root, receipt_path):
    install_runtime(payload, runtime_root, receipt_path, source="home")
    other = tmp_path / "other-payload"
    build_portable_payload(PACKAGE_ROOT, other, version="9.9.9")
    result = repair_runtime(other, runtime_root, receipt_path)
    assert result["status"] == "refused"


def test_repair_with_no_receipt_performs_a_fresh_install(payload, runtime_root, receipt_path):
    result = repair_runtime(payload, runtime_root, receipt_path)
    assert result["status"] == "installed"


def test_every_state_has_a_plain_next_step(payload, runtime_root, receipt_path):
    fresh = install_runtime(payload, runtime_root, receipt_path, source="home")
    noop = install_runtime(payload, runtime_root, receipt_path, source="home")
    for result in (fresh, noop):
        assert isinstance(result.get("next_step"), str) and result["next_step"]
