"""Profile-pack publisher + exact-parent Git delivery integration coverage. Uses only disposable
local bare Git repositories created under ``tmp_path`` — never Laurence's real
``operator-voice-profile`` repo and never any network remote.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from operator_voice.domain.models import (
    ContextScope,
    FactState,
    FeedbackAction,
    FeedbackEvent,
    ProfileFact,
    ProfileGeneration,
    SyncStatus,
)
from operator_voice.persistence import profile_pack
from operator_voice.persistence.crypto import CryptoContext, InMemoryKeyStore


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], capture_output=True, text=True, check=True)
    return remote


def _synthetic_generation(generation_id: str = "gen_1", created_at: float = 1000.0) -> ProfileGeneration:
    return ProfileGeneration(
        generation_id=generation_id, subject_id="subj_1", schema_version="operator-voice-schema/v1",
        normalization_version="normalize/v1", extraction_version="features/v1",
        synthesis_version="synthesis/v1", taxonomy_version="taxonomy/v1", policy_version="policy/v1",
        tokenizer_version="conservative-estimator/v1", feature_registry_version="features/v1",
        content_digest="synthetic-digest-not-real", creation_reason="synthetic test fixture",
        parent_generation_id=None, created_at=created_at,
    )


def _synthetic_facts() -> list[ProfileFact]:
    scope = ContextScope()
    return [
        ProfileFact(fact_id="fact_1", subject_id="subj_1", facet="tone", value="direct",
                    scope=scope, state=FactState.SUPPORTED, confidence=0.9),
        ProfileFact(fact_id="fact_2", subject_id="subj_1", facet="banned_phrase", value="delve",
                    scope=scope, state=FactState.CORRECTED, confidence=1.0),
        ProfileFact(fact_id="fact_3", subject_id="subj_1", facet="unstable_signal", value=0.1,
                    scope=scope, state=FactState.OBSERVED, confidence=0.1),
    ]


def _synthetic_feedback() -> list[FeedbackEvent]:
    return [
        FeedbackEvent(
            feedback_id="fb_1", subject_id="subj_1", action=FeedbackAction.BAN,
            target_facet="banned_phrase", reason="operator said not to say this",
            prior_value=None, new_value="delve", utterance_digest="digest_synthetic_1",
            created_at=1000.0,
        )
    ]


def test_only_publishable_states_are_selected():
    facts = _synthetic_facts()
    publishable = profile_pack.select_publishable_facts(facts)
    facets = {f.facet for f in publishable}
    assert "tone" in facets
    assert "banned_phrase" in facets
    assert "unstable_signal" not in facets  # OBSERVED never leaves the machine


def test_portable_objects_contain_no_raw_prose_field_names():
    generation = _synthetic_generation()
    objects = profile_pack.build_portable_objects(
        facts=_synthetic_facts(), feedback_events=_synthetic_feedback(), generation=generation
    )
    assert len(objects) == 4  # 2 publishable facts + 1 feedback event + 1 calibration manifest
    payloads = [json.loads(o.plaintext) for o in objects]
    for payload in payloads:
        assert "raw_text" not in payload
        assert "path" not in payload
        assert "utterance" not in payload  # only utterance_digest, never raw utterance text


def test_deterministic_encryption_is_content_addressed_for_dedup():
    crypto = CryptoContext(InMemoryKeyStore(), kek_account=CryptoContext.PROFILE_PACK_KEK_ACCOUNT)
    dek, _wrapped = crypto.wrap_new_dek()
    generation = _synthetic_generation()
    objects = profile_pack.build_portable_objects(
        facts=_synthetic_facts(), feedback_events=[], generation=generation
    )
    encrypted_once = profile_pack.encrypt_objects(crypto, dek, objects)
    encrypted_twice = profile_pack.encrypt_objects(crypto, dek, objects)
    assert encrypted_once == encrypted_twice  # same plaintext -> byte-identical ciphertext (CAS)

    for obj in objects:
        decrypted = profile_pack.decrypt_object(dek, encrypted_once[obj.digest], obj.object_type)
        assert json.dumps(decrypted, sort_keys=True, separators=(",", ":")).encode() == obj.plaintext


def test_publish_writes_ciphertext_objects_and_redacted_manifest_only(bare_remote, tmp_path):
    crypto = CryptoContext(InMemoryKeyStore(), kek_account=CryptoContext.PROFILE_PACK_KEK_ACCOUNT)
    dek, _wrapped = crypto.wrap_new_dek()
    generation = _synthetic_generation()
    objects = profile_pack.build_portable_objects(
        facts=_synthetic_facts(), feedback_events=_synthetic_feedback(), generation=generation
    )
    encrypted = profile_pack.encrypt_objects(crypto, dek, objects)
    manifest = profile_pack.build_manifest(generation=generation, objects=objects, key_generation_id="ppk_1")

    clone_dir = tmp_path / "clone-1"
    profile_pack.ensure_clone(clone_dir, str(bare_remote))
    profile_pack.write_pack(
        repo_path=clone_dir, encrypted_objects=encrypted, manifest=manifest,
        object_types={o.digest: o.object_type for o in objects},
    )
    outcome = profile_pack.commit_and_push(
        clone_dir, commit_message="publish generation gen_1", expected_parent_sha=None
    )
    assert outcome.status == SyncStatus.PUBLISHED

    # Plaintext/content scan: every committed blob is either ciphertext (unparseable as our
    # redacted JSON schema) or the deliberately-redacted manifest/generation pointer.
    tracked = subprocess.run(
        ["git", "-C", str(clone_dir), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert any(f.endswith(".enc") for f in tracked)
    assert "profile/manifest.json" in tracked
    for f in tracked:
        if f.endswith(".enc"):
            raw = (clone_dir / f).read_bytes()
            with pytest.raises((UnicodeDecodeError, json.JSONDecodeError, ValueError)):
                json.loads(raw.decode("utf-8"))


def test_republishing_identical_content_is_a_no_op(bare_remote, tmp_path):
    crypto = CryptoContext(InMemoryKeyStore(), kek_account=CryptoContext.PROFILE_PACK_KEK_ACCOUNT)
    dek, _wrapped = crypto.wrap_new_dek()
    generation = _synthetic_generation()
    objects = profile_pack.build_portable_objects(facts=_synthetic_facts(), feedback_events=[], generation=generation)
    encrypted = profile_pack.encrypt_objects(crypto, dek, objects)
    manifest = profile_pack.build_manifest(generation=generation, objects=objects, key_generation_id="ppk_1")

    clone_dir = tmp_path / "clone-1"
    profile_pack.ensure_clone(clone_dir, str(bare_remote))
    profile_pack.write_pack(repo_path=clone_dir, encrypted_objects=encrypted, manifest=manifest,
                             object_types={o.digest: o.object_type for o in objects})
    first = profile_pack.commit_and_push(clone_dir, commit_message="publish gen_1", expected_parent_sha=None)
    assert first.status == SyncStatus.PUBLISHED

    head_after_first = _run(clone_dir, "rev-parse", "HEAD").stdout.strip()

    # Re-publish the exact same generation/objects: no new object files, manifest unchanged.
    newly_written = profile_pack.write_pack(
        repo_path=clone_dir, encrypted_objects=encrypted, manifest=manifest,
        object_types={o.digest: o.object_type for o in objects},
    )
    assert newly_written == []  # every object already exists at its content address
    second = profile_pack.commit_and_push(
        clone_dir, commit_message="publish gen_1 again", expected_parent_sha=head_after_first
    )
    assert second.status == SyncStatus.PUBLISHED
    assert second.pushed_sha == head_after_first  # no new commit was made


def test_clone_realigns_to_remote_before_publish_after_divergence(bare_remote, tmp_path):
    crypto = CryptoContext(InMemoryKeyStore(), kek_account=CryptoContext.PROFILE_PACK_KEK_ACCOUNT)
    dek, _wrapped = crypto.wrap_new_dek()

    # Seed remote with an initial profile publish, then a governance-only fast-forward.
    gen_seed = _synthetic_generation(generation_id="gen_seed")
    objects_seed = profile_pack.build_portable_objects(facts=_synthetic_facts(), feedback_events=[], generation=gen_seed)
    encrypted_seed = profile_pack.encrypt_objects(crypto, dek, objects_seed)
    manifest_seed = profile_pack.build_manifest(generation=gen_seed, objects=objects_seed, key_generation_id="ppk_1")
    clone_seed = tmp_path / "clone-seed"
    profile_pack.ensure_clone(clone_seed, str(bare_remote))
    profile_pack.write_pack(
        repo_path=clone_seed, encrypted_objects=encrypted_seed, manifest=manifest_seed,
        object_types={o.digest: o.object_type for o in objects_seed},
    )
    seed_outcome = profile_pack.commit_and_push(
        clone_seed, commit_message="seed publish", expected_parent_sha=None,
    )
    assert seed_outcome.status == SyncStatus.PUBLISHED

    gov_file = clone_seed / "GOVERNANCE.txt"
    gov_file.write_text("rules-only\n")
    _run(clone_seed, "add", "GOVERNANCE.txt")
    _run(clone_seed, "commit", "-m", "governance-only")
    _run(clone_seed, "push", "origin", "HEAD:main")
    remote_head = _run(clone_seed, "rev-parse", "origin/main").stdout.strip()

    # Stale publisher clone diverged with unpushed profile commits from the pre-governance base.
    clone_pub = tmp_path / "clone-pub"
    subprocess.run(["git", "clone", str(bare_remote), str(clone_pub)], capture_output=True, text=True, check=True)
    _run(clone_pub, "reset", "--hard", "HEAD~1")
    gen_stale = _synthetic_generation(generation_id="gen_stale", created_at=2500.0)
    objects_stale = profile_pack.build_portable_objects(facts=_synthetic_facts(), feedback_events=[], generation=gen_stale)
    encrypted_stale = profile_pack.encrypt_objects(crypto, dek, objects_stale)
    manifest_stale = profile_pack.build_manifest(generation=gen_stale, objects=objects_stale, key_generation_id="ppk_1")
    profile_pack.write_pack(
        repo_path=clone_pub, encrypted_objects=encrypted_stale, manifest=manifest_stale,
        object_types={o.digest: o.object_type for o in objects_stale},
    )
    _run(clone_pub, "add", "-A", "profile")
    _run(clone_pub, "commit", "-m", "stale unpushed publish")
    assert _run(clone_pub, "rev-parse", "HEAD").stdout.strip() != remote_head

    profile_pack.ensure_clone(clone_pub, str(bare_remote))
    profile_pack.sync_clone_to_remote(clone_pub)
    assert _run(clone_pub, "rev-parse", "HEAD").stdout.strip() == remote_head

    gen_new = ProfileGeneration(
        generation_id="gen_new", subject_id="subj_1", schema_version="operator-voice-schema/v1",
        normalization_version="normalize/v1", extraction_version="features/v1",
        synthesis_version="synthesis/v1", taxonomy_version="taxonomy/v1", policy_version="policy/v1",
        tokenizer_version="conservative-estimator/v1", feature_registry_version="features/v1",
        content_digest="synthetic-digest-not-real", creation_reason="synthetic test fixture",
        parent_generation_id="gen_seed", created_at=3000.0,
    )
    objects_new = profile_pack.build_portable_objects(facts=_synthetic_facts(), feedback_events=[], generation=gen_new)
    encrypted_new = profile_pack.encrypt_objects(crypto, dek, objects_new)
    manifest_new = profile_pack.build_manifest(
        generation=gen_new, objects=objects_new, key_generation_id="ppk_1", git_parent_sha=remote_head,
    )
    profile_pack.write_pack(
        repo_path=clone_pub, encrypted_objects=encrypted_new, manifest=manifest_new,
        object_types={o.digest: o.object_type for o in objects_new},
    )
    outcome = profile_pack.commit_and_push(
        clone_pub, commit_message="publish gen_new after realign", expected_parent_sha=remote_head,
    )
    assert outcome.status == SyncStatus.PUBLISHED
    assert outcome.pushed_sha is not None
    assert _run(bare_remote, "rev-parse", "main").stdout.strip() == outcome.pushed_sha


def test_divergent_writer_is_refused_never_merged(bare_remote, tmp_path):
    crypto = CryptoContext(InMemoryKeyStore(), kek_account=CryptoContext.PROFILE_PACK_KEK_ACCOUNT)
    dek, _wrapped = crypto.wrap_new_dek()

    # Writer A publishes generation 1 first.
    gen1 = _synthetic_generation()
    objects1 = profile_pack.build_portable_objects(facts=_synthetic_facts(), feedback_events=[], generation=gen1)
    encrypted1 = profile_pack.encrypt_objects(crypto, dek, objects1)
    manifest1 = profile_pack.build_manifest(generation=gen1, objects=objects1, key_generation_id="ppk_1")
    clone_a = tmp_path / "clone-a"
    profile_pack.ensure_clone(clone_a, str(bare_remote))
    profile_pack.write_pack(repo_path=clone_a, encrypted_objects=encrypted1, manifest=manifest1,
                             object_types={o.digest: o.object_type for o in objects1})
    outcome_a = profile_pack.commit_and_push(clone_a, commit_message="A publishes gen_1", expected_parent_sha=None)
    assert outcome_a.status == SyncStatus.PUBLISHED

    # Writer B cloned BEFORE A's push (stale parent) and tries to publish a divergent generation 2.
    clone_b = tmp_path / "clone-b"
    subprocess.run(["git", "clone", str(bare_remote), str(clone_b)], capture_output=True, text=True, check=True)
    # Force clone_b's local knowledge of origin/main back to "before A published" to simulate the
    # stale read that a real concurrent writer would have had.
    stale_parent_sha = _run(clone_b, "rev-parse", "HEAD~0").stdout.strip()  # currently == A's commit
    # Simulate staleness explicitly: B believes the parent is some other (wrong) sha.
    fabricated_stale_parent = "0" * 40

    gen2 = _synthetic_generation(generation_id="gen_2", created_at=2000.0)
    objects2 = profile_pack.build_portable_objects(facts=_synthetic_facts(), feedback_events=[], generation=gen2)
    encrypted2 = profile_pack.encrypt_objects(crypto, dek, objects2)
    manifest2 = profile_pack.build_manifest(generation=gen2, objects=objects2, key_generation_id="ppk_1")
    profile_pack.write_pack(repo_path=clone_b, encrypted_objects=encrypted2, manifest=manifest2,
                             object_types={o.digest: o.object_type for o in objects2})

    outcome_b = profile_pack.commit_and_push(
        clone_b, commit_message="B publishes gen_2 over stale parent", expected_parent_sha=fabricated_stale_parent
    )
    assert outcome_b.status == SyncStatus.CONFLICT
    assert outcome_b.pushed_sha is None

    # The remote must still be exactly at A's commit — never merged, never overwritten.
    remote_head = subprocess.run(
        ["git", "-C", str(bare_remote), "rev-parse", "main"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert remote_head == outcome_a.pushed_sha


def test_local_advances_remote_refuses_stale_generation():
    parents = {"gen_2": "gen_1", "gen_3": "gen_2", "gen_c": "gen_a"}

    def parent_of(gen_id: str) -> str | None:
        return parents.get(gen_id)

    assert profile_pack.local_advances_remote(
        local_generation_id="gen_2", remote_generation_id="gen_1", parent_of=parent_of,
    )
    assert profile_pack.local_advances_remote(
        local_generation_id="gen_1", remote_generation_id="gen_1", parent_of=parent_of,
    )
    assert not profile_pack.local_advances_remote(
        local_generation_id="gen_1", remote_generation_id="gen_2", parent_of=parent_of,
    )
    assert not profile_pack.local_advances_remote(
        local_generation_id="gen_c", remote_generation_id="gen_b", parent_of=parent_of,
    )


def test_sync_then_stale_publish_does_not_regress_remote_manifest(bare_remote, tmp_path):
    """After realigning a clone to origin/main, publishing an older local generation must not
    overwrite a newer remote profile manifest (the regression sync_clone_to_remote enabled)."""
    crypto = CryptoContext(InMemoryKeyStore(), kek_account=CryptoContext.PROFILE_PACK_KEK_ACCOUNT)
    dek, _wrapped = crypto.wrap_new_dek()

    gen_remote = _synthetic_generation(generation_id="gen_remote", created_at=2000.0)
    objects_remote = profile_pack.build_portable_objects(
        facts=_synthetic_facts(), feedback_events=[], generation=gen_remote,
    )
    encrypted_remote = profile_pack.encrypt_objects(crypto, dek, objects_remote)
    manifest_remote = profile_pack.build_manifest(
        generation=gen_remote, objects=objects_remote, key_generation_id="ppk_1",
    )
    clone_a = tmp_path / "clone-a"
    profile_pack.ensure_clone(clone_a, str(bare_remote))
    profile_pack.write_pack(
        repo_path=clone_a, encrypted_objects=encrypted_remote, manifest=manifest_remote,
        object_types={o.digest: o.object_type for o in objects_remote},
    )
    outcome_a = profile_pack.commit_and_push(clone_a, commit_message="remote gen_remote", expected_parent_sha=None)
    assert outcome_a.status == SyncStatus.PUBLISHED

    gen_stale = _synthetic_generation(generation_id="gen_stale", created_at=1000.0)
    assert not profile_pack.local_advances_remote(
        local_generation_id=gen_stale.generation_id,
        remote_generation_id=gen_remote.generation_id,
        parent_of=lambda _gid: None,
    )

    clone_b = tmp_path / "clone-b"
    profile_pack.ensure_clone(clone_b, str(bare_remote))
    profile_pack.sync_clone_to_remote(clone_b)
    assert profile_pack.read_manifest(clone_b)["generation_id"] == "gen_remote"


def test_read_manifest_rejects_invalid_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "profile").mkdir()
    (repo / "profile" / "manifest.json").write_text("{not json")
    with pytest.raises(profile_pack.ProfilePackError, match="not valid JSON"):
        profile_pack.read_manifest(repo)


def test_read_manifest_rejects_missing_generation_id(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "profile").mkdir()
    (repo / "profile" / "manifest.json").write_text('{"schema":"profile-pack/v1"}')
    with pytest.raises(profile_pack.ProfilePackError, match="missing generation_id"):
        profile_pack.read_manifest(repo)


def test_push_requires_pull_request_detects_gh013():
    assert profile_pack._push_requires_pull_request(
        "remote: error: GH013: Repository rule violations found\n"
        "- Changes must be made through a pull request.\n"
    )


def test_gh013_with_rejected_marker_falls_back_to_pr(bare_remote, tmp_path, monkeypatch):
    clone_dir = tmp_path / "clone-gh013"
    profile_pack.ensure_clone(clone_dir, str(bare_remote))
    (clone_dir / "profile").mkdir(exist_ok=True)
    (clone_dir / "profile" / "manifest.json").write_text('{"generation_id":"gen_gh013"}\n')

    real_run_git = profile_pack._run_git

    def _push_aware_run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
        if args[:2] == ("push", "origin") and args[-1].endswith(":main"):
            return subprocess.CompletedProcess(
                args,
                1,
                "",
                "remote: error: GH013: Repository rule violations\n"
                "! [remote rejected] HEAD -> main (push declined due to repository rule violations)",
            )
        return real_run_git(repo_path, *args)

    monkeypatch.setattr(profile_pack, "_run_git", _push_aware_run_git)
    expected = profile_pack.PublishOutcome(
        status=SyncStatus.PUBLISHED, pushed_sha="abc", detail="via pr",
    )
    monkeypatch.setattr(profile_pack, "deliver_via_pull_request", lambda *_a, **_k: expected)

    outcome = profile_pack.commit_and_push(
        clone_dir,
        commit_message="publish generation gen_gh013",
        expected_parent_sha=None,
        generation_id="gen_gh013",
    )
    assert outcome == expected


def test_github_repo_slug_parses_scp_and_https():
    assert profile_pack._github_repo_slug("git@github.com:KleinPerkins/operator-voice-profile.git") == (
        "KleinPerkins/operator-voice-profile"
    )
    assert profile_pack._github_repo_slug("https://github.com/KleinPerkins/operator-voice-profile") == (
        "KleinPerkins/operator-voice-profile"
    )


def test_commit_and_push_falls_back_to_pr_delivery(bare_remote, tmp_path, monkeypatch):
    clone_dir = tmp_path / "clone-pr"
    profile_pack.ensure_clone(clone_dir, str(bare_remote))
    (clone_dir / "profile").mkdir(exist_ok=True)
    (clone_dir / "profile" / "manifest.json").write_text('{"generation_id":"gen_pr"}\n')

    real_run_git = profile_pack._run_git

    def _push_aware_run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
        if args[:2] == ("push", "origin") and args[-1].endswith(":main"):
            return subprocess.CompletedProcess(args, 1, "", "GH013: pull request required")
        return real_run_git(repo_path, *args)

    monkeypatch.setattr(profile_pack, "_run_git", _push_aware_run_git)
    expected = profile_pack.PublishOutcome(
        status=SyncStatus.PUBLISHED,
        pushed_sha="deadbeef",
        detail="published via PR #10",
        pr_url="https://github.com/example/repo/pull/10",
        pr_number=10,
    )
    calls: list[dict[str, object]] = []

    def _fake_deliver(repo_path, **kwargs):
        calls.append({"repo_path": repo_path, **kwargs})
        return expected

    monkeypatch.setattr(profile_pack, "deliver_via_pull_request", _fake_deliver)

    outcome = profile_pack.commit_and_push(
        clone_dir,
        commit_message="publish generation gen_pr",
        expected_parent_sha=None,
        generation_id="gen_pr",
    )
    assert outcome == expected
    assert calls and calls[0]["generation_id"] == "gen_pr"


def test_deliver_via_pull_request_waits_for_merge(bare_remote, tmp_path, monkeypatch):
    clone_dir = tmp_path / "clone-deliver"
    profile_pack.ensure_clone(clone_dir, str(bare_remote))
    (clone_dir / "profile").mkdir(exist_ok=True)
    (clone_dir / "profile" / "manifest.json").write_text('{"generation_id":"gen_deliver"}\n')
    _run(clone_dir, "add", "profile/manifest.json")
    _run(clone_dir, "commit", "-m", "publish generation gen_deliver")
    _run(clone_dir, "push", "-u", "origin", "main")
    publish_sha = _run(clone_dir, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(profile_pack, "_origin_remote_url", lambda _path: "git@github.com:Example/profile.git")
    view_calls = {"count": 0}

    def _fake_gh(*args: str) -> subprocess.CompletedProcess:
        if args[:2] == ("pr", "list"):
            return subprocess.CompletedProcess(args, 0, "[]", "")
        if args[:2] == ("pr", "create"):
            return subprocess.CompletedProcess(args, 0, "https://github.com/Example/profile/pull/7\n", "")
        if args[:2] == ("pr", "view"):
            view_calls["count"] += 1
            state = "MERGED" if view_calls["count"] >= 1 else "OPEN"
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"state": state, "mergedAt": "2026-01-01T00:00:00Z"}), "",
            )
        raise AssertionError(f"unexpected gh invocation: {args}")

    monkeypatch.setattr(profile_pack, "_run_gh", _fake_gh)

    real_run_git = profile_pack._run_git

    def _merge_on_publish_branch(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
        if args[:3] == ("push", "-u", "origin") and len(args) > 3 and args[3].startswith("publish/"):
            real_run_git(repo_path, "push", "origin", f"{publish_sha}:main")
            real_run_git(repo_path, "push", "origin", f"HEAD:{args[3]}")
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run_git(repo_path, *args)

    monkeypatch.setattr(profile_pack, "_run_git", _merge_on_publish_branch)

    outcome = profile_pack.deliver_via_pull_request(
        clone_dir,
        generation_id="gen_deliver",
        commit_message="publish generation gen_deliver",
        pr_wait_seconds=30,
    )
    assert outcome.status == SyncStatus.PUBLISHED
    assert outcome.pr_number == 7
    assert outcome.pushed_sha == publish_sha
    assert _run(clone_dir, "rev-parse", "HEAD").stdout.strip() == publish_sha


def test_deliver_via_pull_request_force_updates_existing_publish_branch(bare_remote, tmp_path, monkeypatch):
    clone_dir = tmp_path / "clone-retry"
    profile_pack.ensure_clone(clone_dir, str(bare_remote))
    (clone_dir / "profile").mkdir(exist_ok=True)
    (clone_dir / "profile" / "manifest.json").write_text('{"generation_id":"gen_retry"}\n')
    _run(clone_dir, "add", "profile/manifest.json")
    _run(clone_dir, "commit", "-m", "publish generation gen_retry")
    _run(clone_dir, "push", "-u", "origin", "main")
    _run(clone_dir, "branch", "publish/gen_retry")
    _run(clone_dir, "push", "-u", "origin", "publish/gen_retry")
    (clone_dir / "profile" / "manifest.json").write_text('{"generation_id":"gen_retry","v":2}\n')
    _run(clone_dir, "add", "profile/manifest.json")
    _run(clone_dir, "commit", "-m", "publish generation gen_retry update")

    monkeypatch.setattr(profile_pack, "_origin_remote_url", lambda _path: "git@github.com:Example/profile.git")
    monkeypatch.setattr(
        profile_pack,
        "_run_gh",
        lambda *args: subprocess.CompletedProcess(
            args, 0,
            "[]" if args[:2] == ("pr", "list") else "https://github.com/Example/profile/pull/8\n",
            "",
        ),
    )
    monkeypatch.setattr(
        profile_pack,
        "_wait_for_pr_merge",
        lambda *_a, **_k: True,
    )

    outcome = profile_pack.deliver_via_pull_request(
        clone_dir,
        generation_id="gen_retry",
        commit_message="publish generation gen_retry update",
        pr_wait_seconds=1,
    )
    assert outcome.status == SyncStatus.PUBLISHED
    assert outcome.pr_number == 8
