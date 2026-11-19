"""Offline installer preflight honors the locked Python 3.12 minor."""
from __future__ import annotations

import argparse
from collections import namedtuple
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from operator_voice.runtime import install_cli

ROOT = Path(__file__).resolve().parents[2]
Version = namedtuple("Version", "major minor micro releaselevel serial")


@pytest.mark.parametrize("minor,expected", [(11, 1), (12, 0), (13, 1)])
def test_check_rejects_unsupported_python_minor(tmp_path, monkeypatch, capsys, minor, expected):
    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_text("# synthetic payload\n")
    monkeypatch.setattr(install_cli.sys, "version_info", Version(3, minor, 0, "final", 0))
    assert install_cli.cmd_check(argparse.Namespace(source_root=str(source))) == expected
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("ok" if not expected else "failed")
    if expected:
        assert "Python 3.12 is required" in result["problems"][0]
    assert sorted(p.name for p in source.iterdir()) == ["__init__.py"]


@pytest.mark.parametrize("minor,expected", [(12, 0), (13, 2)])
def test_shell_check_uses_supported_interpreter_only(tmp_path, minor, expected):
    """Run the real shell preflight with interpreters that execute its actual -c guard."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(ROOT / "install.sh", checkout / "install.sh")
    shutil.copytree(ROOT / "src", checkout / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    before = {str(p.relative_to(checkout)): p.read_bytes()
              for p in checkout.rglob("*") if p.is_file()}
    script = (
        f"#!{sys.executable}\n"
        "import sys\nfrom collections import namedtuple\n"
        "Version = namedtuple('Version', 'major minor micro releaselevel serial')\n"
        f"sys.version_info = Version(3, {minor}, 0, 'final', 0)\n"
        "args = sys.argv[1:]\n"
        "if args[0] == '-B':\n    sys.dont_write_bytecode = True\n    args = args[1:]\n"
        "assert args[0] == '-c'\nexec(args[1])\n"
    )
    for name in ("python3.12", "python3"):
        path = fake_bin / name
        path.write_text(script)
        path.chmod(0o700)
    env = {key: value for key, value in os.environ.items()
           if key not in {"BASH_ENV", "ENV", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"}}
    env.update({"HOME": str(home), "PATH": f"{fake_bin}:/usr/bin:/bin"})
    result = subprocess.run(["/bin/bash", str(checkout / "install.sh"), "--check"],
                            env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == expected, result.stdout + result.stderr
    if expected:
        assert "Python 3.12 required" in result.stderr
        assert not result.stdout
    else:
        assert json.loads(result.stdout)["status"] == "ok"
    assert not list(home.iterdir())
    assert before == {str(p.relative_to(checkout)): p.read_bytes()
                      for p in checkout.rglob("*") if p.is_file()}


def test_install_refuses_existing_unsupported_venv_before_dependency_changes(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    def interpreter(path, minor):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"#!{sys.executable}\nimport sys\n"
            f"sys.version_info = (3, {minor}, 0)\n"
            "assert sys.argv[1] == '-c'\nexec(sys.argv[2])\n"
        )
        path.chmod(0o700)
    interpreter(fake_bin / "python3.12", 12)
    interpreter(fake_bin / "python3", 12)
    venv = home / ".local/share/operator-voice/venv"
    interpreter(venv / "bin/python", 13)
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    env = {key: value for key, value in os.environ.items() if key not in {"BASH_ENV", "ENV", "XDG_DATA_HOME", "XDG_STATE_HOME"}}
    env.update({"HOME": str(home), "PATH": f"{fake_bin}:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    result = subprocess.run(["/bin/bash", str(ROOT / "install.sh"), "install"], env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 2
    assert "existing venv requires Python 3.12" in result.stderr
    assert before == {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
