"""``runtime.requirements`` is the single source of truth for OVT's runtime dependency pins,
carried inside the portable payload so every distributor (this repo's own ``install.sh``, the
skills-repo GCB shelf's ``install.sh``, and a future cpp-owned generic ``kind: tool`` reconciler)
provisions the SAME pins instead of hardcoding an independent, driftable copy — build plan §8.1/
§8.2 "one runtime, no manager hierarchy" applied to dependency provisioning, not just code bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import operator_voice
from operator_voice.runtime.requirements import load_install_requires, requirements_path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - this repo requires >=3.12
    import tomli as tomllib  # type: ignore[no-redef]

PACKAGE_ROOT = Path(operator_voice.__file__).parent
REPO_ROOT = PACKAGE_ROOT.parents[1]


def _pyproject_dependencies() -> list[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def test_requirements_json_matches_pyproject_dependencies_exactly():
    """The one regression guard that closes the drift class of bug this file exists to prevent:
    a future ``pyproject.toml`` dependency bump that forgets this file must fail CI immediately."""
    assert load_install_requires(PACKAGE_ROOT) == _pyproject_dependencies()


def test_requirements_file_lives_inside_the_payload_allowlist():
    path = requirements_path(PACKAGE_ROOT)
    assert path.exists()
    assert path.suffix == ".json"  # must stay an allowlisted suffix (runtime.payload) to be carried


def test_load_install_requires_rejects_wrong_schema(tmp_path):
    pkg = tmp_path / "operator_voice"
    (pkg / "runtime").mkdir(parents=True)
    (pkg / "runtime" / "requirements.json").write_text('{"schema": "bogus/v1", "install_requires": ["x"]}')
    with pytest.raises(ValueError):
        load_install_requires(pkg)


def test_load_install_requires_rejects_empty_list(tmp_path):
    pkg = tmp_path / "operator_voice"
    (pkg / "runtime").mkdir(parents=True)
    (pkg / "runtime" / "requirements.json").write_text(
        '{"schema": "runtime-requirements/v1", "install_requires": []}'
    )
    with pytest.raises(ValueError):
        load_install_requires(pkg)
