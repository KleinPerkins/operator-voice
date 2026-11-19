"""M3 security/privacy boundary coverage (build plan v4 §13 M3 worklist 3 + acceptance): the
conditional-publication gate and the recovery/idempotency + content-free invariants.

Adversarial focus:
  * LOCAL_ONLY (the default) performs NO network I/O — proven by making every profile_pack network
    primitive raise if the curate path ever touches it.
  * ENCRYPTED_GIT publishes via the existing M1 exact-parent encrypted-Git delivery (against a
    disposable LOCAL bare repo — never a real remote, never the network).
  * A curation run row (and per-source state) is content-free: a marker planted in source text
    never appears in any curation metadata.
  * `off` flipped mid-tick stops cleanly at a source boundary (CANCELLED, no publish), and the work
    already committed stays idempotent.
"""

from __future__ import annotations

from synthetic_sources import write_slack_notes

import json
import re
import subprocess
from pathlib import Path

import pytest

from operator_voice.application.cancellation import set_off
from operator_voice.application.service import OperatorVoiceService
from operator_voice.persistence import profile_pack
from operator_voice.runtime.paths import RuntimePaths

_MARKER = "MARKER-operator-secret-phrase-42"
_NOTES = [
    f"I really think we ship this now. {_MARKER}. The team is ready and I trust them.",
    f"Honestly this is the right call, {_MARKER}, we should go this sprint for sure.",
    f"We ship next week, {_MARKER}; I believe the team is ready and so am I, clearly.",
]


def _extract_literal(instructions: str) -> str:
    m = re.search(r"challenge_literal='([^']+)'", instructions)
    assert m, instructions
    return m.group(1)


def _write_notes(tmp_path: Path, name: str, texts: list[str]) -> Path:
    root = tmp_path / name
    root.mkdir()
    write_slack_notes(root, texts, conversations=len(texts))
    return root


def test_local_only_curate_never_touches_the_network(service_local, tmp_path, monkeypatch):
    svc = service_local
    setup = svc.setup()
    root = _write_notes(tmp_path, "src", _NOTES)
    svc.source_add(subject_id=setup["subject_id"], adapter_type="slack_export", canonical_root=str(root))

    def _boom(*a, **k):
        raise AssertionError("LOCAL_ONLY curate must NOT perform any network/profile_pack I/O")

    for fn in ("ensure_clone", "commit_and_push", "current_remote_head", "write_pack"):
        monkeypatch.setattr(profile_pack, fn, _boom)

    result = svc.curate(trigger="scheduled")
    assert result["status"] == "COMPLETED"
    assert result["documents_new"] == 3
    assert result["published"] is False
    assert result["publish_status"] == "skipped_local_only"


def test_encrypted_git_curate_publishes_via_exact_parent_delivery(migrated_db, in_memory_crypto, tmp_path):
    conn, _ = migrated_db
    paths = RuntimePaths(config_dir=tmp_path / "c", data_dir=tmp_path / "d", state_dir=tmp_path / "s")
    paths.ensure()
    svc = OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto, paths=paths)
    setup = svc.setup()

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], capture_output=True, text=True, check=True)

    # Bind ENCRYPTED_GIT (consequential → confirm with the one-use literal).
    bind1 = svc.profile_remote_bind(remote_url=str(remote))
    literal = _extract_literal(bind1["instructions"])
    bound = svc.profile_remote_bind(remote_url=str(remote), challenge_literal=literal)
    assert bound["mode"] == "ENCRYPTED_GIT"

    root = _write_notes(tmp_path, "src", _NOTES)
    svc.source_add(subject_id=setup["subject_id"], adapter_type="slack_export", canonical_root=str(root))

    result = svc.curate(trigger="scheduled")
    assert result["status"] == "COMPLETED"
    assert result["published"] is True
    assert result["publish_status"] == "PUBLISHED"  # profile_publish() SyncStatus, via curate

    # The disposable bare remote actually advanced to a real published commit.
    head = subprocess.run(["git", "-C", str(remote), "rev-parse", "main"], capture_output=True, text=True, check=True)
    assert head.returncode == 0 and head.stdout.strip()
    unchanged = svc.curate(trigger="scheduled")
    assert unchanged["generation_ids"] == []
    assert unchanged["publish_status"] == "skipped_no_changes"
    after = subprocess.run(["git", "-C", str(remote), "rev-parse", "main"], capture_output=True, text=True, check=True)
    assert after.stdout == head.stdout


def test_curation_metadata_is_content_free(service_local, tmp_path):
    svc = service_local
    setup = svc.setup()
    root = _write_notes(tmp_path, "src", _NOTES)
    svc.source_add(subject_id=setup["subject_id"], adapter_type="slack_export", canonical_root=str(root))
    svc.curate()

    # Every curation_runs / source_curation_state cell must be free of the source marker.
    for table in ("curation_runs", "source_curation_state"):
        rows = svc.conn.execute(f"SELECT * FROM {table}").fetchall()
        blob = json.dumps([[r[k] for k in r.keys()] for r in rows], default=str)
        assert _MARKER not in blob, f"{table} leaked source content"

    # The status API surface is content-free too.
    assert _MARKER not in json.dumps(svc.curation_status(), default=str)


def test_off_mid_tick_cancels_cleanly_without_publishing(service_local, tmp_path, monkeypatch):
    svc = service_local
    setup = svc.setup()
    subject_id = setup["subject_id"]
    root_a = _write_notes(tmp_path, "a", _NOTES)
    root_b = _write_notes(tmp_path, "b", _NOTES)
    svc.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root_a))
    svc.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root_b))

    real = svc._ingest_source_within
    calls = {"n": 0}

    def _wrap(conn, source, **kwargs):
        r = real(conn, source, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            set_off(conn, "operator turned it off mid-tick")  # off flips after the first source
        return r

    monkeypatch.setattr(svc, "_ingest_source_within", _wrap)
    result = svc.curate(trigger="scheduled")
    assert result["status"] == "CANCELLED"
    assert result["publish_status"] == "skipped_cancelled"
    assert result["published"] is False
    assert result["sources_processed"] == 1  # only the first source ran; second was skipped
    assert result["documents_new"] == 3      # first source's committed work survived


def test_encrypted_git_publish_failure_is_contained(migrated_db, in_memory_crypto, tmp_path, monkeypatch):
    """A transient ENCRYPTED_GIT push failure must NOT crash the tick or leave a dangling RUNNING
    row — ingestion is already durable and profile_publish is exact-parent idempotent."""
    conn, _ = migrated_db
    paths = RuntimePaths(config_dir=tmp_path / "c", data_dir=tmp_path / "d", state_dir=tmp_path / "s")
    paths.ensure()
    svc = OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto, paths=paths)
    setup = svc.setup()
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], capture_output=True, text=True, check=True)
    bind1 = svc.profile_remote_bind(remote_url=str(remote))
    svc.profile_remote_bind(remote_url=str(remote), challenge_literal=_extract_literal(bind1["instructions"]))
    root = _write_notes(tmp_path, "src", _NOTES)
    svc.source_add(subject_id=setup["subject_id"], adapter_type="slack_export", canonical_root=str(root))

    def _network_boom(*a, **k):
        raise RuntimeError("simulated transient push failure")

    real_publish = svc.profile_publish
    monkeypatch.setattr(svc, "profile_publish", _network_boom)
    result = svc.curate(trigger="scheduled")

    assert result["status"] == "COMPLETED"           # tick itself succeeded
    assert result["documents_new"] == 3              # ingested work committed
    assert result["published"] is False
    assert result["publish_status"] == "failed:RuntimeError"
    # No dangling RUNNING row (it was finalized, not abandoned).
    running = svc.conn.execute("SELECT COUNT(*) FROM curation_runs WHERE status='RUNNING'").fetchone()[0]
    assert running == 0
    monkeypatch.setattr(svc, "profile_publish", real_publish)
    retry = svc.curate(trigger="scheduled")
    assert retry["documents_new"] == 0
    assert retry["generation_ids"] == []
    assert retry["publish_status"] == "PUBLISHED"


def test_encrypted_git_publish_conflict_is_not_marked_published(migrated_db, in_memory_crypto, tmp_path, monkeypatch):
    conn, _ = migrated_db
    paths = RuntimePaths(config_dir=tmp_path / "c", data_dir=tmp_path / "d", state_dir=tmp_path / "s")
    paths.ensure()
    svc = OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto, paths=paths)
    setup = svc.setup()
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], capture_output=True, text=True, check=True)
    bind1 = svc.profile_remote_bind(remote_url=str(remote))
    svc.profile_remote_bind(remote_url=str(remote), challenge_literal=_extract_literal(bind1["instructions"]))
    root = _write_notes(tmp_path, "src", _NOTES)
    svc.source_add(subject_id=setup["subject_id"], adapter_type="slack_export", canonical_root=str(root))

    monkeypatch.setattr(svc, "profile_publish", lambda **_: {"status": "CONFLICT", "detail": "simulated"})

    result = svc.curate(trigger="scheduled")
    assert result["status"] == "COMPLETED"
    assert result["published"] is False
    assert result["publish_status"] == "CONFLICT"


@pytest.fixture
def service_local(migrated_db, in_memory_crypto):
    conn, _ = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)
