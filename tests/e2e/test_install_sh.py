"""End-to-end coverage of the repo-root ``install.sh`` — the actual SSP installer entrypoint a
human or a future cpp-owned generic ``kind: tool`` reconciler would invoke. Runs the real script
against a disposable ``HOME`` (never the operator's real one); creates a real (throwaway) venv, so
this is slower and network-touching (PyPI) — that is the point: it is the one test proving the
shell entrypoint itself works, not just the pure-Python install/repair logic already covered by
``tests/integration/test_runtime_install.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"


def _run(*args: str, home: Path, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        ["bash", str(INSTALL_SH), *args], cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=180, check=check,
    )


@pytest.fixture
def disposable_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.mark.e2e
def test_check_is_read_only_and_passes(disposable_home: Path):
    result = _run("--check", home=disposable_home)
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    # --check must never write anything under HOME
    assert not any(disposable_home.iterdir())


@pytest.mark.e2e
def test_install_doctor_repair_uninstall_full_cycle(disposable_home: Path):
    install_result = _run(home=disposable_home)
    assert '"status": "installed"' in install_result.stdout

    wrapper = disposable_home / ".local/bin/operator-voice"
    assert wrapper.exists() and os.access(wrapper, os.X_OK)

    status = subprocess.run(
        [str(wrapper), "status"], env={**os.environ, "HOME": str(disposable_home)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    status_payload = json.loads(status.stdout)
    assert status_payload["backend_mode"] == "LOCAL_ONLY"

    doctor_result = _run("--doctor", home=disposable_home)
    assert '"status": "ok"' in doctor_result.stdout

    reinstall_result = _run(home=disposable_home)
    assert '"status": "up_to_date"' in reinstall_result.stdout

    repair_result = _run("--repair", home=disposable_home)
    assert '"status": "ok"' in repair_result.stdout

    _run("--uninstall", home=disposable_home)
    assert not wrapper.exists()
    assert not (disposable_home / ".local/share/operator-voice/runtime").exists()
    # profile DB is left behind by design (uninstall touches runtime only)
    assert (disposable_home / ".local/share/operator-voice/operator-voice.sqlite3").exists()
