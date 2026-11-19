"""local-jobs scheduler-planner unit coverage (build plan v4 §13 M3 worklist 2): OVT integrates
with the ONE local-jobs command instance and builds no scheduler of its own. These tests assert the
rendered argv is a single, valid ``command`` instance and that per-tick configuration travels via
``--env`` (never a ``--``-prefixed ``--arg`` value, which local-jobs' passthrough would drop)."""

from __future__ import annotations

import sys

import pytest

from operator_voice.runtime import scheduler


def test_daily_policy_never_recreates_a_passive_timer():
    plan = scheduler.plan_schedule()
    for argv in (plan.add_argv, plan.update_argv, scheduler.build_repair_job_argv(
            program=plan.program, program_args=plan.program_args, env=plan.env)):
        assert "--interval-seconds" not in argv
        assert argv[argv.index("--execution-mode") + 1] == "on_demand"


def test_plan_is_a_single_command_instance_with_stable_id():
    plan = scheduler.plan_schedule(interval_seconds=3600)
    assert plan.job_id == scheduler.SCHEDULE_JOB_ID
    argv = plan.add_argv
    assert argv[:3] == ["add-job", "--type", "command"]
    assert argv.count("--id") == 1
    assert argv[argv.index("--id") + 1] == "operator-voice-curate"
    assert "--interval-seconds" not in argv
    assert argv[argv.index("--execution-mode") + 1] == "on_demand"


def test_plan_includes_runtime_pythonpath_in_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    expected = str(tmp_path / "share" / "operator-voice" / "runtime" / "current")
    plan = scheduler.plan_schedule(interval_seconds=3600)
    assert plan.env["PYTHONPATH"] == expected
    env_values = [plan.add_argv[i + 1] for i, tok in enumerate(plan.add_argv) if tok == "--env"]
    assert f"PYTHONPATH={expected}" in env_values


def test_tick_config_travels_via_env_not_dashed_arg_values():
    """The local-jobs generic add-job passthrough drops the value after --arg when it begins with
    '--'. So NO --arg value may start with '--', and the trigger/limit must be --env entries."""
    plan = scheduler.plan_schedule(interval_seconds=3600, per_source_limit=250)
    argv = plan.add_argv
    arg_values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--arg"]
    assert arg_values, "expected program args passed via --arg"
    assert all(not v.startswith("--") for v in arg_values), arg_values
    env_values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--env"]
    assert "OVT_CURATE_TRIGGER=manual" in env_values
    assert "OVT_CURATE_PER_SOURCE_LIMIT=250" in env_values


def test_program_is_installed_ovt_venv_not_sys_executable(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    expected = str(tmp_path / "share" / "operator-voice" / "venv" / "bin" / "python")
    monkeypatch.setattr(sys, "executable", "/tmp/ephemeral/worktree/.venv/bin/python3")
    plan = scheduler.plan_schedule()
    assert plan.program == expected
    assert plan.program != sys.executable
    assert plan.program_args[:2] == ["-m", "operator_voice.interfaces.cli"]
    assert plan.program_args[-1] == "curate"


def test_repair_argv_patches_program_args_env_only():
    plan = scheduler.plan_schedule(interval_seconds=3600)
    argv = scheduler.build_repair_job_argv(
        program=plan.program,
        program_args=plan.program_args,
        env={"PYTHONPATH": "/tmp/runtime/current", "OVT_CURATE_TRIGGER": "scheduled", "OVT_CURATE_PER_SOURCE_LIMIT": "99"},
    )
    assert argv[:4] == ["update-job", "--type", "command", "--id"]
    assert "--enabled" not in argv
    assert "--interval-seconds" not in argv
    assert "--display-name" not in argv
    env_values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--env"]
    assert "OVT_CURATE_PER_SOURCE_LIMIT=99" in env_values


def test_merge_repair_env_preserves_operator_keys():
    merged = scheduler.merge_repair_env(
        {"OVT_CURATE_PER_SOURCE_LIMIT": "250", "CUSTOM": "keep"},
        required={"PYTHONPATH": "/tmp/current", "OVT_CURATE_TRIGGER": "scheduled"},
    )
    assert merged == {
        "OVT_CURATE_PER_SOURCE_LIMIT": "250",
        "CUSTOM": "keep",
        "PYTHONPATH": "/tmp/current",
        "OVT_CURATE_TRIGGER": "scheduled",
    }


def test_update_argv_refreshes_existing_instance():
    plan = scheduler.plan_schedule(interval_seconds=3600)
    argv = plan.update_argv
    assert argv[:4] == ["update-job", "--type", "command", "--id"]
    assert argv[argv.index("--id") + 1] == "operator-voice-curate"
    env_values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--env"]
    assert "OVT_CURATE_TRIGGER=manual" in env_values
    assert any(v.startswith("PYTHONPATH=") for v in env_values)


def test_remove_argv_requires_confirm():
    argv = scheduler.build_delete_job_argv()
    assert argv == ["delete-job", "--id", "operator-voice-curate", "--confirm"]


def test_interval_below_minimum_is_rejected():
    with pytest.raises(ValueError):
        scheduler.build_add_job_argv(program="/usr/bin/python3", program_args=["curate"], env={}, interval_seconds=30)
    plan = scheduler.plan_schedule(interval_seconds=30)
    assert plan.warnings  # plan_schedule surfaces it as a warning rather than raising


def test_render_command_quotes_spaces_but_not_simple_tokens():
    rendered = scheduler.render_command(["add-job", "--display-name", "Operator Voice — continual curation"])
    assert "add-job --display-name '" in rendered  # the spaced value is quoted
    assert rendered.startswith("local-jobs.sh ")
