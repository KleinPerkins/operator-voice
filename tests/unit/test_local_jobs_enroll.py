"""Best-effort passive-curation enrollment during setup."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from operator_voice.runtime import local_jobs_enroll, scheduler


def _seed_ovt_runtime(root: Path) -> None:
    venv_python = root / "operator-voice" / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    (root / "operator-voice" / "runtime" / "current").mkdir(parents=True)


@pytest.fixture(autouse=True)
def _disable_live_enroll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OVT_DISABLE_PASSIVE_CURATION_ENROLL", "1")


def test_ensure_skips_when_ovt_venv_missing(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.delenv("OVT_DISABLE_PASSIVE_CURATION_ENROLL", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "resolve_local_jobs_sh", lambda: "/tmp/local-jobs.sh")

    result = local_jobs_enroll.ensure_curation_schedule()
    assert result["status"] == "skipped"
    assert result["reason"] == "ovt_venv_not_installed"


def test_ensure_skips_when_runtime_slot_missing(monkeypatch: pytest.MonkeyPatch, tmp_path):
    share = tmp_path / "share"
    venv_python = share / "operator-voice" / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(share))
    monkeypatch.delenv("OVT_DISABLE_PASSIVE_CURATION_ENROLL", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "resolve_local_jobs_sh", lambda: "/tmp/local-jobs.sh")

    result = local_jobs_enroll.ensure_curation_schedule()
    assert result["status"] == "skipped"
    assert result["reason"] == "ovt_runtime_not_installed"


def test_ensure_skips_when_env_disabled():
    result = local_jobs_enroll.ensure_curation_schedule()
    assert result["status"] == "skipped"
    assert result["reason"] == "disabled_by_env"


def test_ensure_skips_when_local_jobs_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OVT_DISABLE_PASSIVE_CURATION_ENROLL", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "resolve_local_jobs_sh", lambda: None)
    result = local_jobs_enroll.ensure_curation_schedule()
    assert result["status"] == "skipped"
    assert result["reason"] == "local_jobs_not_installed"


def test_ensure_repairs_when_already_registered(monkeypatch: pytest.MonkeyPatch, tmp_path):
    share = tmp_path / "share"
    _seed_ovt_runtime(share)
    monkeypatch.setenv("XDG_DATA_HOME", str(share))
    expected_program = str(share / "operator-voice" / "venv" / "bin" / "python")
    monkeypatch.delenv("OVT_DISABLE_PASSIVE_CURATION_ENROLL", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "resolve_local_jobs_sh", lambda: "/tmp/local-jobs.sh")
    monkeypatch.setattr(
        local_jobs_enroll,
        "_load_job_entry",
        lambda **_: {
            "id": scheduler.SCHEDULE_JOB_ID,
            "program": "/tmp/ephemeral/worktree/.venv/bin/python3",
            "env": {"OVT_CURATE_PER_SOURCE_LIMIT": "250", "OVT_CURATE_TRIGGER": "scheduled"},
            "enabled": False,
        },
    )

    captured: dict[str, list[str]] = {}

    def _run(local_jobs_sh, argv, *args, **kwargs):
        captured["argv"] = list(argv)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(local_jobs_enroll, "_run_local_jobs", _run)

    result = local_jobs_enroll.ensure_curation_schedule()
    assert result["status"] == "repaired"
    assert result["job_id"] == scheduler.SCHEDULE_JOB_ID
    assert captured["argv"][0:2] == ["update-job", "--type"]
    assert "--enabled" not in captured["argv"]
    assert "--interval-seconds" not in captured["argv"]
    assert captured["argv"][captured["argv"].index("--program") + 1] == expected_program
    env_values = [captured["argv"][i + 1] for i, tok in enumerate(captured["argv"]) if tok == "--env"]
    assert "OVT_CURATE_PER_SOURCE_LIMIT=250" in env_values


def test_ensure_registers_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path):
    share = tmp_path / "share"
    _seed_ovt_runtime(share)
    monkeypatch.setenv("XDG_DATA_HOME", str(share))
    monkeypatch.setattr(local_jobs_enroll, "_load_job_entry", lambda **_: None)
    monkeypatch.delenv("OVT_DISABLE_PASSIVE_CURATION_ENROLL", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "resolve_local_jobs_sh", lambda: "/tmp/local-jobs.sh")

    proc = MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(local_jobs_enroll, "_run_local_jobs", lambda *a, **k: proc)

    result = local_jobs_enroll.ensure_curation_schedule(interval_seconds=3600)
    assert result["status"] == "registered"
    assert result["job_id"] == scheduler.SCHEDULE_JOB_ID
    assert result["interval_seconds"] == 0
    assert result["execution_mode"] == "on_demand"


def test_ensure_surfaces_add_job_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    share = tmp_path / "share"
    _seed_ovt_runtime(share)
    monkeypatch.setenv("XDG_DATA_HOME", str(share))
    monkeypatch.setattr(local_jobs_enroll, "_load_job_entry", lambda **_: None)
    monkeypatch.delenv("OVT_DISABLE_PASSIVE_CURATION_ENROLL", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "resolve_local_jobs_sh", lambda: "/tmp/local-jobs.sh")

    proc = MagicMock(returncode=1, stdout="", stderr="instance already exists: operator-voice-curate")
    monkeypatch.setattr(local_jobs_enroll, "_run_local_jobs", lambda *a, **k: proc)

    result = local_jobs_enroll.ensure_curation_schedule()
    assert result["status"] == "failed"
    assert "already exists" in result["detail"]


def test_ensure_fails_actionably_when_local_jobs_python_unresolved(monkeypatch: pytest.MonkeyPatch, tmp_path):
    share = tmp_path / "share"
    _seed_ovt_runtime(share)
    monkeypatch.setenv("XDG_DATA_HOME", str(share))
    monkeypatch.setattr(local_jobs_enroll, "_load_job_entry", lambda **_: None)
    monkeypatch.delenv("OVT_DISABLE_PASSIVE_CURATION_ENROLL", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "resolve_local_jobs_sh", lambda: "/tmp/local-jobs.sh")
    monkeypatch.setattr(local_jobs_enroll, "resolve_local_jobs_python", lambda **_: None)

    result = local_jobs_enroll.ensure_curation_schedule()
    assert result["status"] == "failed"
    assert "PyYAML" in result["detail"]


def test_ensure_submits_planned_job_to_local_jobs(monkeypatch: pytest.MonkeyPatch, tmp_path):
    share = tmp_path / "share"
    _seed_ovt_runtime(share)
    monkeypatch.setenv("XDG_DATA_HOME", str(share))
    monkeypatch.setattr(local_jobs_enroll, "_load_job_entry", lambda **_: None)
    monkeypatch.delenv("OVT_DISABLE_PASSIVE_CURATION_ENROLL", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "resolve_local_jobs_sh", lambda: "/tmp/local-jobs.sh")

    captured: dict[str, Any] = {}

    def _run(local_jobs_sh, argv, *, timeout):
        captured["local_jobs_sh"] = local_jobs_sh
        captured["argv"] = list(argv)
        captured["timeout"] = timeout
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(local_jobs_enroll, "_run_local_jobs", _run)

    result = local_jobs_enroll.ensure_curation_schedule()
    assert result["status"] == "registered"
    assert captured["local_jobs_sh"] == "/tmp/local-jobs.sh"


def test_resolve_local_jobs_python_uses_path_python_before_uv(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LOCAL_JOBS_PYTHON", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "_path_python_candidates", lambda: ["/opt/homebrew/bin/python3"])
    monkeypatch.setattr(local_jobs_enroll, "_python_has_yaml", lambda path: path == "/opt/homebrew/bin/python3")
    monkeypatch.setattr(local_jobs_enroll.Path, "is_file", lambda path: str(path) == "/opt/homebrew/bin/python3")

    assert local_jobs_enroll.resolve_local_jobs_python(local_jobs_sh="/tmp/local-jobs.sh") == "/opt/homebrew/bin/python3"


def test_resolve_local_jobs_python_uses_uv_fallback_when_no_path_python_has_yaml(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LOCAL_JOBS_PYTHON", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "_path_python_candidates", lambda: [])
    monkeypatch.setattr(local_jobs_enroll, "_python_has_yaml", lambda _: False)
    monkeypatch.setattr(
        local_jobs_enroll,
        "_resolve_uv_python_with_yaml",
        lambda: "/tmp/uv-python",
    )

    assert local_jobs_enroll.resolve_local_jobs_python(local_jobs_sh="/tmp/local-jobs.sh") == "/tmp/uv-python"


def test_resolve_uv_python_with_yaml_installs_into_ovt_venv(monkeypatch: pytest.MonkeyPatch, tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    class _Paths:
        data_dir = tmp_path

    monkeypatch.setattr(local_jobs_enroll, "default_paths", lambda: _Paths())
    monkeypatch.setattr(local_jobs_enroll.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    captured: dict[str, list[str]] = {}
    yaml_state = {"installed": False}

    def _has_yaml(path: str) -> bool:
        return yaml_state["installed"] and path == str(venv_python)

    def _run(argv, *args, **kwargs):
        captured["argv"] = list(argv)
        if argv[:2] == ["uv", "pip"]:
            yaml_state["installed"] = True
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(local_jobs_enroll, "_python_has_yaml", _has_yaml)
    monkeypatch.setattr(local_jobs_enroll.subprocess, "run", _run)

    assert local_jobs_enroll._resolve_uv_python_with_yaml() == str(venv_python)
    assert captured["argv"] == ["uv", "pip", "install", "--python", str(venv_python), "pyyaml"]


def test_resolve_local_jobs_python_uses_uv_fallback_when_system_python_lacks_yaml(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LOCAL_JOBS_PYTHON", raising=False)
    monkeypatch.setattr(local_jobs_enroll, "_path_python_candidates", lambda: ["/usr/bin/python3"])
    monkeypatch.setattr(local_jobs_enroll, "_python_has_yaml", lambda _: False)
    monkeypatch.setattr(
        local_jobs_enroll,
        "_resolve_uv_python_with_yaml",
        lambda: "/tmp/uv-python",
    )

    assert local_jobs_enroll.resolve_local_jobs_python(local_jobs_sh="/tmp/local-jobs.sh") == "/tmp/uv-python"


def test_job_registered_parses_view_jobs_json(monkeypatch: pytest.MonkeyPatch):
    payload = {"instances": [{"id": scheduler.SCHEDULE_JOB_ID}]}
    proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
    monkeypatch.setattr(local_jobs_enroll, "_run_local_jobs", lambda *a, **k: proc)
    assert local_jobs_enroll._job_registered(local_jobs_sh="/tmp/local-jobs.sh", job_id=scheduler.SCHEDULE_JOB_ID)
