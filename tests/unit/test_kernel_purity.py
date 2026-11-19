"""Structural guard — build plan invariant §7/D7: "the deterministic kernel keeps zero model/
network dependency" and "model calls never occur inside the kernel."

This is a *structural* test (parses the source, does not execute it) so the invariant holds even
for code paths that unit tests do not exercise. Every module under ``operator_voice.kernel`` must
import NOTHING from the gateway, the application layer, the adapters, the interfaces, or any
networking / HTTP / model-provider library.
"""

from __future__ import annotations

import ast
import pathlib

_KERNEL_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "operator_voice" / "kernel"

# Import prefixes a pure deterministic kernel module may never pull in.
_FORBIDDEN_INTERNAL_PREFIXES = (
    "operator_voice.application",
    "operator_voice.adapters",
    "operator_voice.interfaces",
    "operator_voice.persistence",
    "operator_voice.runtime",
)
_FORBIDDEN_NETWORK_MODULES = {
    "socket", "http", "http.client", "urllib", "urllib.request", "requests", "httpx", "aiohttp",
    "ssl", "ftplib", "smtplib", "asyncio", "openai", "anthropic", "boto3", "grpc",
}


def _iter_kernel_modules() -> list[pathlib.Path]:
    return sorted(p for p in _KERNEL_DIR.glob("*.py") if p.name != "__init__.py")


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module)
    return names


def test_kernel_modules_exist():
    mods = _iter_kernel_modules()
    assert mods, "expected kernel modules to scan"


def test_kernel_imports_no_application_or_network():
    offenders: dict[str, set[str]] = {}
    for path in _iter_kernel_modules():
        bad: set[str] = set()
        for imp in _imports(path):
            if imp in _FORBIDDEN_NETWORK_MODULES:
                bad.add(imp)
            if any(imp == p or imp.startswith(p + ".") for p in _FORBIDDEN_INTERNAL_PREFIXES):
                bad.add(imp)
            if imp == "operator_voice.application.gateway" or imp.endswith(".gateway"):
                bad.add(imp)
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"kernel purity violated (§7/D7): {offenders}"
