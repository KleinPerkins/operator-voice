"""Own pending publication acknowledgement; disposable local Git only."""
import json
import subprocess

import pytest

from operator_voice.application.service import OperatorVoiceService
from operator_voice.domain.models import BackendMode, ProfileBackendConfig, ProfileSyncState, SyncStatus
from operator_voice.persistence import profile_pack, store
from operator_voice.runtime.paths import RuntimePaths


@pytest.fixture
def pending_profile(migrated_db, in_memory_crypto, tmp_path, monkeypatch):
    conn, _ = migrated_db
    paths = RuntimePaths(config_dir=tmp_path / "c", data_dir=tmp_path / "d", state_dir=tmp_path / "s")
    paths.ensure()
    svc = OperatorVoiceService(conn, in_memory_crypto, paths=paths)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    publisher = tmp_path / "publisher"
    profile_pack.ensure_clone(publisher, str(remote))
    def commit(generation):
        directory = publisher / "profile"
        directory.mkdir(exist_ok=True)
        (directory / "manifest.json").write_text(json.dumps({"generation_id": generation}))
        for args in (("add", "."), ("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                                     "commit", "-m", "synthetic publication"), ("push", "origin", "HEAD:main")):
            subprocess.run(["git", "-C", str(publisher), *args], check=True, capture_output=True)
        return subprocess.check_output(["git", "-C", str(publisher), "rev-parse", "HEAD"], text=True).strip()
    old = commit("gen_old")
    store.set_backend_config(conn, ProfileBackendConfig(mode=BackendMode.ENCRYPTED_GIT, active=True,
                                                       remote_reference_token=str(remote)))
    store.set_sync_state(conn, ProfileSyncState(local_generation_id="gen_pending", remote_generation_id="gen_old",
                                               exact_parent_sha=old, publish_status=SyncStatus.PENDING_PUBLICATION))
    conn.commit()
    def forbid(*args, **kwargs):
        raise AssertionError("pull must not publish, create a PR, or wait")
    for name in ("commit_and_push", "deliver_via_pull_request", "_wait_for_pr_merge"):
        monkeypatch.setattr(profile_pack, name, forbid)
    return svc, commit


def test_pull_acknowledges_own_merged_pending_publication(pending_profile):
    svc, commit = pending_profile
    head = commit("gen_pending")
    result = svc.profile_pull()
    assert result == {"status": "up_to_date", "remote_head": head}
    state = store.get_sync_state(svc.conn)
    assert state.publish_status == SyncStatus.PUBLISHED
    assert state.remote_generation_id == state.local_generation_id == "gen_pending"
    assert state.exact_parent_sha == head
    assert svc.profile_pull() == result


@pytest.mark.parametrize("prior_status", [SyncStatus.PENDING_PUBLICATION, SyncStatus.PUBLISHED, SyncStatus.CONFLICT])
def test_pull_keeps_unrelated_remote_advance_a_conflict(pending_profile, prior_status):
    svc, commit = pending_profile
    state = store.get_sync_state(svc.conn)
    state.publish_status = prior_status
    store.set_sync_state(svc.conn, state)
    svc.conn.commit()
    commit("gen_unrelated")
    assert svc.profile_pull()["status"] == "conflict"
    after = store.get_sync_state(svc.conn)
    assert after.publish_status == SyncStatus.CONFLICT
    assert after.exact_parent_sha == state.exact_parent_sha
    assert after.remote_generation_id == "gen_old"


def test_pull_does_not_acknowledge_unmerged_pending_publication(pending_profile):
    svc, _ = pending_profile
    before = store.get_sync_state(svc.conn)
    assert svc.profile_pull() == {"status": "up_to_date", "remote_head": before.exact_parent_sha}
    assert store.get_sync_state(svc.conn) == before


def test_pull_acknowledgement_error_preserves_pending_state(pending_profile, monkeypatch):
    svc, commit = pending_profile
    commit("gen_pending")
    before = store.get_sync_state(svc.conn)
    def fail(*args, **kwargs):
        raise profile_pack.ProfilePackError("synthetic fetch failure")
    monkeypatch.setattr(profile_pack, "try_finalize_pending_publication", fail)
    with pytest.raises(profile_pack.ProfilePackError, match="synthetic fetch failure"):
        svc.profile_pull()
    assert store.get_sync_state(svc.conn) == before


def test_pull_rejects_acknowledgement_for_a_different_head(pending_profile, monkeypatch):
    svc, commit = pending_profile
    commit("gen_unrelated")
    monkeypatch.setattr(profile_pack, "try_finalize_pending_publication", lambda *a, **k:
                        profile_pack.PublishOutcome(SyncStatus.PUBLISHED, "different-head", "synthetic"))
    assert svc.profile_pull()["status"] == "conflict"
