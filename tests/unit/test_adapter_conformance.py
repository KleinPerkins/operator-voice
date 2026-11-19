"""Adapter conformance — build plan v4 §13 M4 acceptance-evidence ("adapter conformance passes")
and worklist 1 ("each behind the same source contract").

Drives EVERY registered adapter through the shared source contract via
:mod:`operator_voice.adapters.conformance`. This is the executable form of M4's "add only
demand-proven adapters": rather than shipping speculative email/Slack/Docs adapters with no
demonstrated demand, we prove the property the plan actually asserts — every registered adapter
obeys role authority, canonical-path safety, resource ceilings, the registration guard,
determinism, quarantine-not-abort, and no persistence coupling.
"""

from __future__ import annotations

from operator_voice.adapters import conformance, registry
from operator_voice.domain.models import AdapterType


def test_every_registered_adapter_passes_conformance(tmp_path):
    reports = conformance.run_all(tmp_path)
    # Every adapter in the registry produced a report...
    assert set(reports) == set(registry._REGISTRY)  # noqa: SLF001
    failures = {rep.adapter_type: rep.failures for rep in reports.values() if not rep.ok}
    assert not failures, f"adapter conformance failures: {failures}"


def test_conformance_covers_each_contract_dimension(tmp_path):
    reports = conformance.run_all(tmp_path)
    required = {
        "registration_guard", "subject_capture", "role_authority_no_relabel",
        "deterministic", "quarantine_not_abort", "resource_ceiling",
        "canonical_path_safety", "no_persistence_import",
    }
    for rep in reports.values():
        assert required.issubset(set(rep.checks)), f"{rep.adapter_type} missing checks: {required - set(rep.checks)}"


def test_registering_an_adapter_without_a_profile_fails_loudly(tmp_path, monkeypatch):
    # Guard: a newly-registered adapter with no conformance profile must fail, forcing a profile to
    # be authored alongside any new adapter (so "conformance passes" can't silently skip it).
    monkeypatch.setitem(registry._REGISTRY, "PHANTOM_ADAPTER", lambda *a, **k: None)  # noqa: SLF001
    reports = conformance.run_all(tmp_path)
    phantom = reports["PHANTOM_ADAPTER"]
    assert phantom.ok is False
    assert phantom.checks["profile_present"].status == "fail"


def test_slack_export_adapter_conformance_report_passes_every_dimension(tmp_path):
    """OVT Slack Adapter v1 (plan §5: 'extend test_adapter_conformance.py'). ``slack_export`` is
    single-role by construction (every message already passed server-side author verification at
    ``slack.import`` time — see ``test_slack_materialize.py``), so it is held to the same bar as
    the retained adapters: every candidate must carry the adapter's one authoritative subject role."""
    reports = conformance.run_all(tmp_path)
    report = reports[AdapterType.SLACK_EXPORT]
    assert report.ok, f"slack_export conformance failures: {report.failures}"
    assert report.checks["role_authority_no_relabel"].status == "pass"
    assert report.checks["subject_capture"].status == "pass"
    assert report.checks["resource_ceiling"].status == "pass"
    assert report.checks["no_persistence_import"].status == "pass"


def test_drive_export_adapter_conformance_report_passes_every_dimension(tmp_path):
    """D-M1-6 (docs/plans/gmail-drive-channel-adapters-v2.md §3.7). ``drive_export`` is single-role
    by construction (every file already passed server-side owner + not-shared-drive verification at
    ``drive.import`` time — see ``test_drive_materialize.py``), so it is held to the same bar as
    ``slack_export``/the retained adapters: every candidate carries the adapter's one authoritative subject
    role, and the file-tree safety dimensions (oversize/symlink) are genuinely exercised."""
    reports = conformance.run_all(tmp_path)
    report = reports[AdapterType.DRIVE_EXPORT]
    assert report.ok, f"drive_export conformance failures: {report.failures}"
    assert report.checks["role_authority_no_relabel"].status == "pass"
    assert report.checks["subject_capture"].status == "pass"
    assert report.checks["resource_ceiling"].status == "pass"
    assert report.checks["canonical_path_safety"].status in {"pass", "n/a"}
    assert report.checks["no_persistence_import"].status == "pass"


def test_conformance_suffix_map_has_explicit_drive_entry():
    """D-M1-9 (v2 — new): the file-tree safety-fixture suffix map must have an EXPLICIT
    ``DRIVE_EXPORT: ".md"`` entry, not the ``.txt`` fallback — otherwise the oversize/symlink safety
    fixtures would be written with a suffix ``drive_export`` filters out, so its §4.4 traversal /
    resource-ceiling guards would never actually run."""
    assert conformance._FILE_TREE_SAFETY_SUFFIX.get(AdapterType.DRIVE_EXPORT) == ".md"  # noqa: SLF001


def test_gmail_export_adapter_conformance_report_passes_every_dimension(tmp_path):
    """G-M2-5: ``gmail_export`` conformance bar matches Slack/Drive."""
    reports = conformance.run_all(tmp_path)
    report = reports[AdapterType.GMAIL_EXPORT]
    assert report.ok, f"gmail_export conformance failures: {report.failures}"
    assert report.checks["role_authority_no_relabel"].status == "pass"
    assert report.checks["subject_capture"].status == "pass"


def test_conformance_suffix_map_has_explicit_gmail_entry():
    """G-M2-6: explicit ``GMAIL_EXPORT: ".md"`` entry, not the ``.txt`` fallback."""
    assert conformance._FILE_TREE_SAFETY_SUFFIX.get(AdapterType.GMAIL_EXPORT) == ".md"  # noqa: SLF001
