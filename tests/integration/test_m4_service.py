"""M4 application-service e2e — build plan v4 §13 M4 worklist + acceptance evidence.

Covers, against a real migrated SQLite DB + in-memory-Keychain crypto and disposable temp HOMEs:
EXTERNAL enrollment/insight gating and refusals, deterministic alias collision review, the
render-only dashboard status source (proved a pure read), deterministic encrypted portability
export→import round-trip fidelity (+ operator-authoritative protection + wrong-passphrase safety),
and multi-machine ENCRYPTED_GIT reconciliation across two simulated replicas with a shared bare
remote (key isolation, key transfer, disjoint merge, and conflicting-correction → review).

Every fixture is SYNTHETIC. No real person's data, communications, or PII is used anywhere; the
"external subject" is a fictional persona.
"""

from __future__ import annotations

from synthetic_sources import write_slack_notes

import json
import re
import subprocess
from pathlib import Path

import pytest

from operator_voice.application.gateway import CallableProvider
from operator_voice.application.service import OperatorVoiceService, ServiceError
from operator_voice.domain.models import (
    AdapterType,
    ContextScope,
    FactState,
    ProfileFact,
    SubjectKind,
    SubjectRegistration,
    SyncStatus,
)
from operator_voice.domain.operations import Group, OPERATIONS
from operator_voice.persistence import migrations, profile_pack, store
from operator_voice.persistence.crypto import CryptoContext, InMemoryKeyStore
from operator_voice.persistence.sqlite import connect
from operator_voice.runtime.paths import RuntimePaths

_NOTES = [
    "I think we should ship this feature next week. It is ready, and the team agrees. Let's go.",
    "Honestly, I think this is the right call. We should ship it soon, and I trust the team here.",
    "We should definitely ship this next sprint. I believe the team is ready, and so am I, clearly.",
]


def _extract_literal(instructions: str) -> str:
    m = re.search(r"challenge_literal='([^']+)'", instructions)
    assert m, f"no challenge_literal in: {instructions!r}"
    return m.group(1)


def _machine(tmp_path: Path, name: str, *, with_paths: bool = False, providers=None) -> OperatorVoiceService:
    """A fully independent OVT 'machine': its own DB, its own content KEK, and its own DISTINCT
    profile-pack KEK (a fresh InMemoryKeyStore → a different key), so cross-machine key isolation is
    real (one machine cannot read another's pack without an explicit key transfer)."""
    conn = connect(tmp_path / f"{name}.sqlite3")
    migrations.migrate(conn)
    content = CryptoContext(InMemoryKeyStore())
    pack = CryptoContext(InMemoryKeyStore(), kek_account=CryptoContext.PROFILE_PACK_KEK_ACCOUNT)
    paths = None
    if with_paths:
        root = tmp_path / f"{name}-runtime"
        paths = RuntimePaths(config_dir=root / "config", data_dir=root / "data", state_dir=root / "state")
        paths.ensure()
    return OperatorVoiceService(conn, content, profile_pack_crypto=pack, paths=paths, providers=providers)


def _write_notes(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    write_slack_notes(root, _NOTES, conversations=len(_NOTES))
    return root


# ======================================================================================
# EXTERNAL subject analysis + enrollment gating + egress boundary
# ======================================================================================


def test_external_enrollment_rejects_disallowed_purposes(tmp_path):
    svc = _machine(tmp_path, "m")
    svc.setup()
    for bad in ["write as them", "predict what they will decide", "infer their political affiliation", "should we hire them"]:
        with pytest.raises(ServiceError):
            svc.subject_add(display_label="Fictional Person", kind="EXTERNAL", purposes=[bad])
    # a genuine analysis purpose enrolls fine
    ok = svc.subject_add(display_label="Fictional Person", kind="EXTERNAL",
                         purposes=["historical communication-pattern analysis"])
    assert ok["kind"] == "EXTERNAL"
    assert ok["generation_permission"] is False  # external can NEVER hold generation permission


def test_insights_refuses_operator_subject(tmp_path):
    svc = _machine(tmp_path, "m")
    setup = svc.setup()
    with pytest.raises(ServiceError):
        svc.subject_insights(subject_id=setup["subject_id"], purpose="analyze how they write")


def test_generation_context_surface_refuses_external_subject(tmp_path):
    """Acceptance: 'every public/direct-service attempt to obtain external-subject generation context
    refuses.' context.get is the generation-context surface; it is OPERATOR-only and refuses an
    EXTERNAL subject outright (an EXTERNAL subject can never receive generation context)."""
    svc = _machine(tmp_path, "m")
    svc.setup()
    ext = svc.subject_add(display_label="Fictional Persona", kind="EXTERNAL", purposes=["analysis"])
    with pytest.raises(ServiceError):
        svc.context_get(subject_id=ext["subject_id"], consumer_id="host-agent", purpose="draft an email")


def test_insights_excludes_denied_facets_and_warns(tmp_path):
    """Acceptance: 'explicit protected/sensitive-trait prompts refuse AND denied facets never appear.'
    Even if a high-risk facet somehow existed on the profile, it is excluded from the bundle and the
    exclusion is surfaced (never silent)."""
    svc = _machine(tmp_path, "m")
    svc.setup()
    ext = svc.subject_add(display_label="Fictional Persona", kind="EXTERNAL",
                          purposes=["historical communication-pattern analysis"])
    sid = ext["subject_id"]
    # A neutral communication-pattern fact (allowed) + a high-risk denylisted facet (must be excluded).
    store.upsert_fact(svc.conn, svc.crypto, ProfileFact(
        fact_id="ok", subject_id=sid, facet="sentence_length", value="short", scope=ContextScope(),
        state=FactState.SUPPORTED, confidence=0.85, source_count=2, document_count=6))
    store.upsert_fact(svc.conn, svc.crypto, ProfileFact(
        fact_id="bad", subject_id=sid, facet="personality", value="type-X", scope=ContextScope(),
        state=FactState.SUPPORTED, confidence=0.9, source_count=3, document_count=9))
    bundle = svc.subject_insights(subject_id=sid, purpose="understand communication patterns")
    facets = {o["facet"] for o in bundle["observations"]}
    assert "sentence_length" in facets
    assert "personality" not in facets  # denied facet never appears
    assert any("excluded" in w.lower() for w in bundle["warnings"])


def test_insights_refuses_unenrolled_external_subject(tmp_path):
    svc = _machine(tmp_path, "m")
    svc.setup()
    # Build an EXTERNAL subject that was never enrolled (no reviewed_at / approved purposes).
    raw = SubjectRegistration.new_external("Unenrolled Persona", [])
    raw.reviewed_at = None
    raw.approved_purposes = []
    store.insert_subject(svc.conn, raw)
    bundle = svc.subject_insights(subject_id=raw.subject_id, purpose="analyze communication style")
    assert bundle["schema"].startswith("subject-insight-bundle/")
    assert any("REFUSED" in w for w in bundle["warnings"])
    assert bundle["observations"] == []


def test_insights_returns_evidence_bounded_bundle_without_generation_instructions(tmp_path):
    svc = _machine(tmp_path, "m")
    svc.setup()
    ext = svc.subject_add(display_label="Fictional Persona", kind="EXTERNAL",
                          purposes=["historical communication-pattern analysis"])
    sid = ext["subject_id"]
    # Seed SYNTHETIC calibrated facts for the external persona.
    for facet, val in [("sentence_length", "short"), ("tone", "direct")]:
        store.upsert_fact(svc.conn, svc.crypto, ProfileFact(
            fact_id=f"f_{facet}", subject_id=sid, facet=facet, value=val, scope=ContextScope(),
            state=FactState.SUPPORTED, confidence=0.85, source_count=2, document_count=6))
    bundle = svc.subject_insights(subject_id=sid, purpose="understand their communication patterns")
    assert bundle["schema"].startswith("subject-insight-bundle/")
    assert bundle["observations"], "expected evidence-bounded observations"
    # STRUCTURAL invariant: an insight bundle can never carry generation/authoring instructions.
    assert "instructions" not in bundle and "ranked_facts" not in bundle
    assert any("proxy" in w.lower() for w in bundle["warnings"])  # standing proxy-risk warning
    # Acceptance: reports show sample size + uncertainty. Every observation is evidence-bounded:
    # it carries an explicit uncertainty phrase and at least one counter-evidence caveat.
    for o in bundle["observations"]:
        assert o["uncertainty"].strip()
        assert o["counter_evidence"], "each observation must carry counter-evidence"


def test_external_egress_denied_even_with_operator_egress_enabled_end_to_end(tmp_path):
    """M4 acceptance: 'EXTERNAL signals cannot leave the machine without separate approval' — proved
    end-to-end. Operator egress ON, EXTERNAL egress OFF, provider configured → the external-insight
    gateway attempt is DENIED_EXTERNAL_EGRESS and analysis stays local."""
    provider = CallableProvider("judge", ("m",), lambda model, signal, purpose: "OK")
    svc = _machine(tmp_path, "m", providers={"judge": provider})
    svc.setup()
    svc.gateway_configure(provider="judge", model="m", allowed_data_classes=["operator", "external"],
                          enable_operator_egress=True, enable_external_egress=False)
    ext = svc.subject_add(display_label="Fictional Persona", kind="EXTERNAL",
                          purposes=["historical communication-pattern analysis"])
    sid = ext["subject_id"]
    store.upsert_fact(svc.conn, svc.crypto, ProfileFact(
        fact_id="f1", subject_id=sid, facet="tone", value="warm", scope=ContextScope(),
        state=FactState.SUPPORTED, confidence=0.8, source_count=2, document_count=4))

    bundle = svc.subject_insights(subject_id=sid, purpose="understand communication patterns")
    assert bundle["observations"]  # local deterministic analysis still produced
    assert any("denied" in w.lower() and "external" in w.lower() for w in bundle["warnings"])

    ledger = svc.gateway_calls()
    ext_calls = [c for c in ledger["calls"] if c["data_class"] == "external"]
    assert ext_calls, "expected an EXTERNAL egress attempt to be recorded"
    assert all(c["outcome"] == "DENIED_EXTERNAL_EGRESS" for c in ext_calls)
    # zero payload retention: the content-free signal never persists a fact value/name
    assert "warm" not in json.dumps(ledger)


def test_external_egress_allowed_only_when_the_separate_switch_is_on(tmp_path):
    provider = CallableProvider("judge", ("m",), lambda model, signal, purpose: "summary-ok")
    svc = _machine(tmp_path, "m", providers={"judge": provider})
    svc.setup()
    svc.gateway_configure(provider="judge", model="m", allowed_data_classes=["operator", "external"],
                          enable_operator_egress=True, enable_external_egress=True)
    ext = svc.subject_add(display_label="Fictional Persona", kind="EXTERNAL",
                          purposes=["historical communication-pattern analysis"])
    sid = ext["subject_id"]
    store.upsert_fact(svc.conn, svc.crypto, ProfileFact(
        fact_id="f1", subject_id=sid, facet="tone", value="warm", scope=ContextScope(),
        state=FactState.SUPPORTED, confidence=0.8, source_count=2, document_count=4))
    bundle = svc.subject_insights(subject_id=sid, purpose="understand communication patterns")
    ext_calls = [c for c in svc.gateway_calls()["calls"] if c["data_class"] == "external"]
    assert ext_calls and all(c["outcome"] == "SUCCESS" for c in ext_calls)
    assert any("permitted" in w.lower() for w in bundle["warnings"])


# ======================================================================================
# Alias collision review
# ======================================================================================


def test_alias_collision_is_quarantined_not_silently_rebound(tmp_path):
    svc = _machine(tmp_path, "m")
    svc.setup()
    e1 = svc.subject_add(display_label="Persona One", kind="EXTERNAL", purposes=["analysis"])["subject_id"]
    e2 = svc.subject_add(display_label="Persona Two", kind="EXTERNAL", purposes=["analysis"])["subject_id"]

    first = svc.subject_alias_add(subject_id=e1, source_type="email", source_native_id="alice@example.com")
    assert first["status"] == "added" and first["verification_state"] == "operator_attested"
    # idempotent re-add to the SAME subject
    again = svc.subject_alias_add(subject_id=e1, source_type="email", source_native_id="alice@example.com")
    assert again["status"] == "exists"
    # SAME identifier to a DIFFERENT subject → collision, quarantined for review (never rebinds).
    collide = svc.subject_alias_add(subject_id=e2, source_type="email", source_native_id="alice@example.com")
    assert collide["status"] == "collision"
    assert collide["verification_state"] == "collision"
    assert collide["collision_review"]


def test_alias_add_refuses_operator_subject(tmp_path):
    svc = _machine(tmp_path, "m")
    setup = svc.setup()
    with pytest.raises(ServiceError):
        svc.subject_alias_add(subject_id=setup["subject_id"], source_type="email", source_native_id="me@example.com")


# ======================================================================================
# Render-only dashboard status source (proved a pure read)
# ======================================================================================


def test_dashboard_data_is_registered_read_safe():
    op = next(o for o in OPERATIONS if o.name == "dashboard.data")
    assert op.group == Group.READ  # registry-level guarantee: not a mutation


def test_dashboard_data_is_a_pure_read_and_redacts_external_labels(tmp_path):
    svc = _machine(tmp_path, "m")
    setup = svc.setup()
    src = svc.source_add(subject_id=setup["subject_id"], adapter_type="slack_export", canonical_root=str(_write_notes(tmp_path, "notes")))
    svc.ingest(source_id=src["source_id"])
    ext = svc.subject_add(display_label="Fictional Persona", kind="EXTERNAL", purposes=["analysis"])

    def _state_signature():
        subs = svc.subject_list()["subjects"]
        total_facts = sum(len(store.list_facts(svc.conn, svc.crypto, s["subject_id"])) for s in subs)
        return (len(subs), total_facts)

    before = _state_signature()
    snap1 = svc.dashboard_data()
    snap2 = svc.dashboard_data()
    after = _state_signature()

    assert before == after  # calling the status source mutated nothing
    snap1.pop("generated_at"); snap2.pop("generated_at")
    assert snap1 == snap2   # deterministic, pure read
    assert snap1["schema"] == "operator-voice-dashboard/v1"
    # EXTERNAL subject label is redacted in the status source (no named person leaves the kernel).
    ext_rows = [it for it in snap1["subjects"]["items"] if it["kind"] == "EXTERNAL"]
    assert ext_rows and all(it["label"] == "EXTERNAL subject" for it in ext_rows)
    assert ext["subject_id"] in {it["subject_id"] for it in ext_rows}


# ======================================================================================
# Portability — deterministic encrypted export/import round-trip
# ======================================================================================


def test_portability_export_import_round_trip_fidelity(tmp_path):
    a = _machine(tmp_path, "A")
    setup_a = a.setup()
    sid_a = setup_a["subject_id"]
    a.ingest(source_id=a.source_add(subject_id=sid_a, adapter_type="slack_export",
                                    canonical_root=str(_write_notes(tmp_path, "notesA")))["source_id"])
    a.fact_correct(subject_id=sid_a, facet="tone", new_value="always direct", reason="operator says so")

    dest = tmp_path / "profile.ovtpack.json"
    first = a.portability_export(destination=str(dest), passphrase="a-strong-passphrase")
    assert first["status"] == "confirmation_required"
    literal = _extract_literal(first["instructions"])
    exported = a.portability_export(destination=str(dest), passphrase="a-strong-passphrase", challenge_literal=literal)
    assert exported["status"] == "exported" and dest.exists()

    # Fresh machine B imports it.
    b = _machine(tmp_path, "B")
    b.setup()
    result = b.portability_import(source=str(dest), passphrase="a-strong-passphrase")
    assert result["status"] == "imported"
    assert result["subjects_added"] >= 1 and result["facts_imported"] >= 1

    def _fact_set(svc, sid):
        return {(f["facet"], str(f["value"]), f["state"], f["scope"]) for f in svc.profile_inspect(subject_id=sid)["facts"]}

    assert _fact_set(a, sid_a) == _fact_set(b, sid_a)  # round-trip fidelity for A's subject
    # the operator-authoritative correction survived the round trip
    b_tone = [f for f in b.profile_inspect(subject_id=sid_a, facet="tone")["facts"] if f["state"] == "CORRECTED"]
    assert b_tone and b_tone[0]["value"] == "always direct"


def test_import_never_overwrites_local_operator_authoritative_facts(tmp_path):
    a = _machine(tmp_path, "A")
    sid_a = a.setup()["subject_id"]
    a.ingest(source_id=a.source_add(subject_id=sid_a, adapter_type="slack_export",
                                    canonical_root=str(_write_notes(tmp_path, "notesA")))["source_id"])
    a.fact_correct(subject_id=sid_a, facet="tone", new_value="A-machine value", reason="A said so")
    dest = tmp_path / "p.json"
    lit = _extract_literal(a.portability_export(destination=str(dest), passphrase="pw-12345678")["instructions"])
    a.portability_export(destination=str(dest), passphrase="pw-12345678", challenge_literal=lit)

    b = _machine(tmp_path, "B")
    b.setup()
    b.portability_import(source=str(dest), passphrase="pw-12345678")  # first import brings A's tone
    # B makes its OWN correction (P0) on the same subject/facet.
    b.fact_correct(subject_id=sid_a, facet="tone", new_value="B-operator override", reason="B said so")
    # Re-import the SAME archive: A's tone must NOT overwrite B's local correction.
    again = b.portability_import(source=str(dest), passphrase="pw-12345678")
    assert again["facts_skipped_operator_authoritative"] >= 1
    tone = [f for f in b.profile_inspect(subject_id=sid_a, facet="tone")["facts"] if f["state"] == "CORRECTED"]
    assert tone and tone[0]["value"] == "B-operator override"


def test_import_wrong_passphrase_fails_safely_with_no_partial_import(tmp_path):
    a = _machine(tmp_path, "A")
    sid_a = a.setup()["subject_id"]
    a.ingest(source_id=a.source_add(subject_id=sid_a, adapter_type="slack_export",
                                    canonical_root=str(_write_notes(tmp_path, "notesA")))["source_id"])
    dest = tmp_path / "p.json"
    lit = _extract_literal(a.portability_export(destination=str(dest), passphrase="right-passphrase")["instructions"])
    a.portability_export(destination=str(dest), passphrase="right-passphrase", challenge_literal=lit)

    b = _machine(tmp_path, "B")
    b.setup()
    before = len(b.subject_list()["subjects"])
    with pytest.raises(ServiceError):
        b.portability_import(source=str(dest), passphrase="WRONG-passphrase")
    assert len(b.subject_list()["subjects"]) == before  # nothing imported on failure


# ======================================================================================
# Multi-machine reconciliation (ENCRYPTED_GIT, shared bare remote)
# ======================================================================================


def _bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], capture_output=True, text=True, check=True)
    return remote


def _inject_pack_key_from_a_to_b(a: OperatorVoiceService, b: OperatorVoiceService) -> str:
    """Simulate the portability KEY transfer at the store layer (portability.import does this via an
    encrypted archive; here we inject directly so B keeps a single operator, isolating the reconcile
    decision under test). Returns the shared key_generation_id."""
    manifest = json.loads((a.paths.profile_pack_clone_dir / "profile" / "manifest.json").read_text())
    gen_id = manifest["key_generation_id"]
    dek = store.dek_for_generation(a.conn, a.profile_pack_crypto, gen_id)
    store.import_key_generation(b.conn, b.profile_pack_crypto, gen_id, dek, purpose=store.PROFILE_PACK_KEY_PURPOSE)
    return gen_id


def test_reconcile_key_isolation_then_transfer_then_merge_and_conflict(tmp_path):
    remote = _bare_remote(tmp_path)

    # Machine A: ingest, correct two facets (operator-authoritative), bind + publish.
    a = _machine(tmp_path, "A", with_paths=True)
    sid_a = a.setup()["subject_id"]
    a.ingest(source_id=a.source_add(subject_id=sid_a, adapter_type="slack_export",
                                    canonical_root=str(_write_notes(tmp_path, "notesA")))["source_id"])
    a.fact_correct(subject_id=sid_a, facet="tone", new_value="A-terse", reason="A said so")
    a.fact_correct(subject_id=sid_a, facet="banned_phrase", new_value="delve", reason="A bans it")
    a.profile_remote_bind(remote_url=str(remote),
                          challenge_literal=_extract_literal(a.profile_remote_bind(remote_url=str(remote))["instructions"]))
    assert a.profile_publish()["status"] == "PUBLISHED"

    # Machine B: own operator, own DISTINCT pack KEK, bound to the SAME remote.
    b = _machine(tmp_path, "B", with_paths=True)
    sid_b = b.setup()["subject_id"]
    b.profile_remote_bind(remote_url=str(remote),
                          challenge_literal=_extract_literal(b.profile_remote_bind(remote_url=str(remote))["instructions"]))

    # (1) Key isolation: B cannot read A's pack before any key transfer.
    isolated = b.profile_reconcile()
    assert isolated["status"] == "key_unavailable"

    # (2) Transfer the pack key (portability does this via an encrypted archive).
    _inject_pack_key_from_a_to_b(a, b)

    # B makes its OWN correction on 'tone' (a P0 correction that must NOT be silently overwritten).
    b.fact_correct(subject_id=sid_b, facet="tone", new_value="B-warm", reason="B said so")

    # (3) Reconcile: disjoint remote facts merge; the conflicting 'tone' correction stops for review.
    result = b.profile_reconcile()
    assert result["status"] == "conflict"
    assert result["conflict_count"] >= 1
    assert any(c["reason"] == "conflicting_correction" and c["facet"] == "tone" for c in result["conflicts"])
    assert "banned_phrase" in result["adopted_facets"]  # A-only corrected fact was merged in
    assert result["merged_count"] >= 1

    # B's own correction is intact (never overwritten by the replica).
    b_tone = [f for f in b.profile_inspect(subject_id=sid_b, facet="tone")["facts"] if f["state"] == "CORRECTED"]
    assert b_tone and b_tone[0]["value"] == "B-warm"
    # the adopted disjoint fact is now present locally
    b_banned = [f for f in b.profile_inspect(subject_id=sid_b, facet="banned_phrase")["facts"] if f["facet"] == "banned_phrase"]
    assert b_banned and b_banned[0]["value"] == "delve"
    # a review item was opened for the conflict (conflicts stop for a human)
    assert b.review_list(subject_id=sid_b)["count"] >= 1


def test_profile_publish_refuses_stale_local_generation_after_remote_advanced(tmp_path):
    remote = _bare_remote(tmp_path)

    a = _machine(tmp_path, "A", with_paths=True)
    sid_a = a.setup()["subject_id"]
    a.ingest(source_id=a.source_add(subject_id=sid_a, adapter_type="slack_export",
                                    canonical_root=str(_write_notes(tmp_path, "notesA")))["source_id"])
    a.profile_remote_bind(remote_url=str(remote),
                          challenge_literal=_extract_literal(a.profile_remote_bind(remote_url=str(remote))["instructions"]))
    assert a.profile_publish()["status"] == "PUBLISHED"

    a.ingest(source_id=a.source_add(subject_id=sid_a, adapter_type="slack_export",
                                    canonical_root=str(_write_notes(tmp_path, "notesA2")))["source_id"])
    assert a.profile_publish()["status"] == "PUBLISHED"
    remote_manifest = json.loads((a.paths.profile_pack_clone_dir / "profile" / "manifest.json").read_text())
    remote_gen_id = remote_manifest["generation_id"]

    b = _machine(tmp_path, "B", with_paths=True)
    sid_b = b.setup()["subject_id"]
    b.ingest(source_id=b.source_add(subject_id=sid_b, adapter_type="slack_export",
                                    canonical_root=str(_write_notes(tmp_path, "notesB")))["source_id"])
    b.profile_remote_bind(remote_url=str(remote),
                          challenge_literal=_extract_literal(b.profile_remote_bind(remote_url=str(remote))["instructions"]))
    _inject_pack_key_from_a_to_b(a, b)

    local_gen = store.get_latest_generation(b.conn, sid_b)
    assert local_gen is not None
    assert local_gen.generation_id != remote_gen_id

    outcome = b.profile_publish()
    assert outcome["status"] == "CONFLICT"
    assert remote_gen_id in outcome["detail"]
    assert json.loads((a.paths.profile_pack_clone_dir / "profile" / "manifest.json").read_text())["generation_id"] == remote_gen_id


def test_profile_publish_updates_remote_generation_id(tmp_path):
    remote = _bare_remote(tmp_path)

    svc = _machine(tmp_path, "M", with_paths=True)
    sid = svc.setup()["subject_id"]
    svc.ingest(source_id=svc.source_add(subject_id=sid, adapter_type="slack_export",
                                       canonical_root=str(_write_notes(tmp_path, "notes")))["source_id"])
    svc.profile_remote_bind(remote_url=str(remote),
                            challenge_literal=_extract_literal(svc.profile_remote_bind(remote_url=str(remote))["instructions"]))
    assert svc.profile_publish()["status"] == "PUBLISHED"

    sync = store.get_sync_state(svc.conn)
    assert sync.local_generation_id == sync.remote_generation_id
    assert sync.local_generation_id is not None
    assert sync.publish_status == SyncStatus.PUBLISHED


def test_profile_publish_conflict_preserves_exact_parent_sha(tmp_path, monkeypatch):
    remote = _bare_remote(tmp_path)

    svc = _machine(tmp_path, "M", with_paths=True)
    sid = svc.setup()["subject_id"]
    svc.ingest(source_id=svc.source_add(subject_id=sid, adapter_type="slack_export",
                                       canonical_root=str(_write_notes(tmp_path, "notes")))["source_id"])
    svc.profile_remote_bind(remote_url=str(remote),
                            challenge_literal=_extract_literal(svc.profile_remote_bind(remote_url=str(remote))["instructions"]))
    assert svc.profile_publish()["status"] == "PUBLISHED"
    anchor = store.get_sync_state(svc.conn).exact_parent_sha
    assert anchor

    svc.ingest(source_id=svc.source_add(subject_id=sid, adapter_type="slack_export",
                                       canonical_root=str(_write_notes(tmp_path, "notes2")))["source_id"])

    def _conflict(*_args, **_kwargs):
        return profile_pack.PublishOutcome(status=SyncStatus.CONFLICT, pushed_sha=None, detail="simulated push conflict")

    monkeypatch.setattr(profile_pack, "commit_and_push", _conflict)
    assert svc.profile_publish()["status"] == "CONFLICT"
    assert store.get_sync_state(svc.conn).exact_parent_sha == anchor


def test_reconcile_without_conflicts_stamps_generation_and_allows_publish(tmp_path):
    remote = _bare_remote(tmp_path)

    a = _machine(tmp_path, "A", with_paths=True)
    sid_a = a.setup()["subject_id"]
    a.ingest(source_id=a.source_add(subject_id=sid_a, adapter_type="slack_export",
                                    canonical_root=str(_write_notes(tmp_path, "notesA")))["source_id"])
    a.fact_correct(subject_id=sid_a, facet="banned_phrase", new_value="delve", reason="A bans it")
    a.profile_remote_bind(remote_url=str(remote),
                          challenge_literal=_extract_literal(a.profile_remote_bind(remote_url=str(remote))["instructions"]))
    assert a.profile_publish()["status"] == "PUBLISHED"

    b = _machine(tmp_path, "B", with_paths=True)
    sid_b = b.setup()["subject_id"]
    b.ingest(source_id=b.source_add(subject_id=sid_b, adapter_type="slack_export",
                                    canonical_root=str(_write_notes(tmp_path, "notesB")))["source_id"])
    b.profile_remote_bind(remote_url=str(remote),
                          challenge_literal=_extract_literal(b.profile_remote_bind(remote_url=str(remote))["instructions"]))
    _inject_pack_key_from_a_to_b(a, b)

    result = b.profile_reconcile()
    assert result["status"] == "reconciled"
    assert b.profile_publish()["status"] == "PUBLISHED"


def test_reconcile_skipped_for_local_only_operator(tmp_path):
    svc = _machine(tmp_path, "m", with_paths=True)
    svc.setup()
    assert svc.profile_reconcile()["status"] == "skipped"


# ======================================================================================
# setup() — idempotent slack_export source enrollment (§ OVT Slack Adapter 1c)
# ======================================================================================


def test_setup_registers_slack_export_source_when_paths_available(tmp_path):
    svc = _machine(tmp_path, "m", with_paths=True)
    result = svc.setup()

    assert result["slack_source"]["status"] == "added"
    assert svc.paths.slack_export_dir.is_dir()

    sources = store.list_sources(svc.conn, svc.crypto, result["subject_id"])
    slack_sources = [s for s in sources if s.adapter_type == AdapterType.SLACK_EXPORT]
    assert len(slack_sources) == 1
    assert slack_sources[0].canonical_root == str(svc.paths.slack_export_dir)
    assert slack_sources[0].active is True


def test_setup_slack_export_enrollment_is_idempotent(tmp_path):
    svc = _machine(tmp_path, "m", with_paths=True)
    first = svc.setup()
    assert first["slack_source"]["status"] == "added"

    second = svc.setup()
    assert second["slack_source"]["status"] == "already_registered"

    sources = store.list_sources(svc.conn, svc.crypto, first["subject_id"])
    slack_sources = [s for s in sources if s.adapter_type == AdapterType.SLACK_EXPORT]
    assert len(slack_sources) == 1  # never duplicated across repeated setup() calls


def test_setup_without_paths_skips_slack_export_enrollment_but_still_completes(tmp_path):
    svc = _machine(tmp_path, "m", with_paths=False)
    result = svc.setup()

    assert result["status"] == "ready"  # setup itself is unaffected (backward compatible)
    assert result["slack_source"] == {"status": "skipped", "reason": "no_runtime_paths"}


# ======================================================================================
# setup() — idempotent drive_export source enrollment (§ OVT Drive Adapter 1c)
# ======================================================================================


def test_setup_registers_drive_export_source_when_paths_available(tmp_path):
    svc = _machine(tmp_path, "m", with_paths=True)
    result = svc.setup()

    assert result["drive_source"]["status"] == "added"
    assert svc.paths.drive_export_dir.is_dir()

    sources = store.list_sources(svc.conn, svc.crypto, result["subject_id"])
    drive_sources = [s for s in sources if s.adapter_type == AdapterType.DRIVE_EXPORT]
    assert len(drive_sources) == 1
    assert drive_sources[0].canonical_root == str(svc.paths.drive_export_dir)
    assert drive_sources[0].active is True


def test_setup_drive_export_enrollment_is_idempotent(tmp_path):
    svc = _machine(tmp_path, "m", with_paths=True)
    first = svc.setup()
    assert first["drive_source"]["status"] == "added"

    second = svc.setup()
    assert second["drive_source"]["status"] == "already_registered"

    sources = store.list_sources(svc.conn, svc.crypto, first["subject_id"])
    drive_sources = [s for s in sources if s.adapter_type == AdapterType.DRIVE_EXPORT]
    assert len(drive_sources) == 1


# ======================================================================================
# setup() — idempotent gmail_export source enrollment (§ OVT Gmail Adapter 1c)
# ======================================================================================


def test_setup_registers_gmail_export_source_when_paths_available(tmp_path):
    svc = _machine(tmp_path, "m", with_paths=True)
    result = svc.setup()

    assert result["gmail_source"]["status"] == "added"
    assert svc.paths.gmail_export_dir.is_dir()

    sources = store.list_sources(svc.conn, svc.crypto, result["subject_id"])
    gmail_sources = [s for s in sources if s.adapter_type == AdapterType.GMAIL_EXPORT]
    assert len(gmail_sources) == 1
    assert gmail_sources[0].canonical_root == str(svc.paths.gmail_export_dir)
    assert gmail_sources[0].active is True


def test_setup_gmail_export_enrollment_is_idempotent(tmp_path):
    svc = _machine(tmp_path, "m", with_paths=True)
    first = svc.setup()
    assert first["gmail_source"]["status"] == "added"

    second = svc.setup()
    assert second["gmail_source"]["status"] == "already_registered"

    sources = store.list_sources(svc.conn, svc.crypto, first["subject_id"])
    gmail_sources = [s for s in sources if s.adapter_type == AdapterType.GMAIL_EXPORT]
    assert len(gmail_sources) == 1
