"""Application-service integration coverage — build plan v3 §6: the sole business-logic/mutation
boundary. Exercises the full ingest -> synthesis -> retrieval loop end-to-end against a real
migrated SQLite DB and in-memory-Keychain crypto, plus feedback precedence, consequential-op
confirmation gating, and off/on cancellation. Every fixture text is synthetic, written for this
test suite."""

from __future__ import annotations

from synthetic_sources import write_slack_notes

import json
import re
from pathlib import Path

import pytest

from operator_voice.adapters import registry as adapter_registry
from operator_voice.adapters.base import AdapterScanResult, make_candidate
from operator_voice.application.service import ConfirmationRequired, OperatorVoiceService, ServiceError
from operator_voice.application.transactions import OperationRefused
from operator_voice.domain.models import AdapterType
from operator_voice.persistence import store


@pytest.fixture
def service(migrated_db, in_memory_crypto):
    conn, _db_path = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


_counter = {"n": 0}


def test_status_reports_installed_version_and_brief(service):
    import operator_voice
    result = service.status()
    assert result["version"] == operator_voice.__version__
    assert result["brief"].startswith(f"operator-voice {operator_voice.__version__}")
    assert result["brief"] == f"operator-voice {operator_voice.__version__}: {result['next_step']}"


def test_context_get_emitted_payload_fits_budget_and_has_no_inline_provenance(service, tmp_path):
    from operator_voice.kernel.retrieval import EMITTED_JSON_SEPARATORS, estimate_tokens
    subject_id = service.setup()["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    service.ingest(source_id=service.source_add(
        subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))["source_id"])
    for budget in (200, 800):
        payload = service.context_get(subject_id=subject_id, consumer_id="test-consumer", token_budget=budget)
        emitted = json.dumps(payload, separators=EMITTED_JSON_SEPARATORS, default=str)
        assert estimate_tokens(emitted) <= payload["actual_tokens"] <= budget
        assert payload["provenance_refs"] == []
        assert all(f["provenance_ids"] == [] for f in payload["ranked_facts"])


@pytest.mark.parametrize("budget", [199, 10**12, True, False, 800.0, "800", None])
def test_context_get_rejects_budgets_outside_the_allowed_range(service, budget):
    subject_id = service.setup()["subject_id"]
    with pytest.raises(ServiceError, match="whole number"):
        service.context_get(subject_id=subject_id, consumer_id="test-consumer", token_budget=budget)


def test_context_get_reports_oversized_metadata_as_service_error(service):
    subject_id = service.setup()["subject_id"]
    with pytest.raises(ServiceError, match="minimum_budget_tokens:"):
        service.context_get(subject_id=subject_id, consumer_id="c" * 4000, token_budget=200)


def test_config_view_reports_configured_gateway_state(service):
    from operator_voice.domain.models import GatewayConfig
    assert service.config_view()["gateway"] == "disabled"
    store.set_gateway_config(service.conn, GatewayConfig(operator_egress_enabled=True))
    assert service.config_view()["gateway"] == "enabled"


def test_backend_copy_is_consistent_across_status_reads(service):
    from operator_voice.domain.models import BackendMode, ProfileBackendConfig
    store.set_backend_config(service.conn, ProfileBackendConfig(mode=BackendMode.ENCRYPTED_GIT))
    assert "encrypted copy syncs" in service.status()["state"]
    assert service.status()["state"] == service.doctor()["backend_health"]
    assert service.status()["state"] == service.profile_backend_status()["health"]


def _write_slack_notes(tmp_path: Path, texts: list[str]) -> Path:
    _counter["n"] += 1
    root = tmp_path / f"slack-drop-{_counter['n']}"
    root.mkdir()
    write_slack_notes(root, texts, conversations=len(texts))
    return root


# A handful of synthetic, distinct-enough "operator voice" paragraphs so Tier-1 stylometric
# features (sentence length, punctuation rate, function-word rate) have real signal to converge
# on across >=3 documents/1 source (build plan default calibration: min_documents_for_supported=3).
def _extract_literal(instructions: str) -> str:
    m = re.search(r"challenge_literal='([^']+)'", instructions)
    assert m, f"could not find a quoted challenge_literal in: {instructions!r}"
    return m.group(1)


_SYNTHETIC_NOTES = [
    "I think we should ship this feature next week. It is ready, and the team agrees. Let's go.",
    "Honestly, I think this is the right call. We should ship it soon, and I trust the team here.",
    "We should definitely ship this next sprint. I believe the team is ready, and so am I, clearly.",
]


def test_setup_creates_operator_subject(service):
    result = service.setup(display_label="Test Operator")
    assert result["status"] == "ready"
    assert result["backend"] == "LOCAL_ONLY"
    subjects = service.subject_list()["subjects"]
    assert len(subjects) == 1
    assert subjects[0]["display_label"] == "Test Operator"


def test_ingest_end_to_end_produces_facts_and_generation(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))
    source_id = source["source_id"]

    result = service.ingest(source_id=source_id)
    assert result["status"] == "ingested"
    assert result["documents_new"] == 3
    assert result["documents_quarantined"] == 0
    assert result["observations_new"] > 0
    assert result["generation_id"] is not None

    inspected = service.profile_inspect(subject_id=subject_id)
    facets = {f["facet"] for f in inspected["facts"]}
    assert "sentence_length" in facets
    assert inspected["generation_count"] == 1


def test_ingest_threads_output_type_from_candidate_into_context_scope(service, tmp_path, monkeypatch):
    """D-M1-0 (docs/plans/gmail-drive-channel-adapters-v2.md §3.7): a candidate's ``output_type``
    (e.g. a Google Doc tagged ``"doc"``) must survive into the PERSISTED ``ContextScope`` — both the
    stored document context and the synthesized fact scope — instead of silently defaulting to
    ``"text"``. Regression for the ``_ingest_source_within`` drop that made §3.6's doc/slide/email
    output-type tagging inert."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = tmp_path / "output-type-src"
    root.mkdir()
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))
    source_id = source["source_id"]

    # A fake scanner that stands in for a (future) output-type-tagging adapter: it emits genuine
    # operator-authored candidates tagged output_type="doc" without needing any Drive/Gmail code.
    def _fake_scan(registration, cursor, limit=None):
        candidates = [
            make_candidate(
                subject_id=registration.subject_id,
                source_id=registration.source_id,
                source_native_id=f"docfile:{i}",
                role="user",
                raw_text=text,
                locator=f"doc:{i}",
                channel="docs",
                output_type="doc",
            )
            for i, text in enumerate(_SYNTHETIC_NOTES)
        ]
        return AdapterScanResult(
            candidates=candidates, next_cursor="docfile:zzz",
            files_scanned=len(candidates), records_scanned=len(candidates),
        )

    monkeypatch.setitem(adapter_registry._REGISTRY, AdapterType.SLACK_EXPORT, _fake_scan)
    result = service.ingest(source_id=source_id)
    assert result["documents_new"] == len(_SYNTHETIC_NOTES)

    conn, crypto = service.conn, service.crypto

    # (1) the PERSISTED document context carries output_type, not the "text" default.
    doc_ids = store.list_document_ids_for_source(conn, source_id)
    assert doc_ids
    for doc_id in doc_ids:
        doc = store.get_document(conn, crypto, doc_id)
        assert doc.context.output_type == "doc", doc.context.as_dict()

    # (2) output_type survives synthesis into the persisted fact scope (the real profile artifact).
    facts = store.list_facts(conn, crypto, subject_id)
    assert facts, "expected synthesized facts from the ingested candidates"
    assert all(f.scope.output_type == "doc" for f in facts), [f.scope.as_dict() for f in facts]


def test_ingest_is_idempotent_on_second_call_with_same_content(service, tmp_path):
    setup = service.setup()
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    source = service.source_add(subject_id=setup["subject_id"], adapter_type="slack_export", canonical_root=str(root))
    first = service.ingest(source_id=source["source_id"])
    assert first["documents_new"] == 3
    second = service.ingest(source_id=source["source_id"])
    # cursor already advanced past every file; nothing new to ingest.
    assert second["documents_new"] == 0
    assert second["observations_new"] == 0


def test_context_get_abstains_with_no_facts_and_returns_facts_once_ingested(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    bundle = service.context_get(subject_id=subject_id, consumer_id="test-consumer")
    assert bundle["abstained"] is True

    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))
    service.ingest(source_id=source["source_id"])

    bundle2 = service.context_get(subject_id=subject_id, consumer_id="test-consumer")
    assert bundle2["schema"].startswith("voice-context-bundle/")
    assert "raw_text" not in json.dumps(bundle2)  # no raw samples by default, §7


def test_context_get_refuses_external_subject(service):
    setup = service.setup()
    external = service.subject_add(display_label="A friend", kind="EXTERNAL", purposes=["analysis_of_own_text"])
    with pytest.raises(ServiceError):
        service.context_get(subject_id=external["subject_id"], consumer_id="test-consumer")


def test_ai_authored_and_quoted_text_contribute_zero_operator_evidence(service, tmp_path, monkeypatch):
    subject_id = service.setup()["subject_id"]
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(tmp_path))
    def scan(reg, cursor, limit=None):
        return AdapterScanResult(next_cursor=None, candidates=[make_candidate(
            subject_id=subject_id, source_id=reg.source_id, source_native_id=str(i),
            role=role, raw_text=text, locator=f"slack:test:{i}", channel="slack",
        ) for i, (role, text) in enumerate([
            ("user", _SYNTHETIC_NOTES[0]),
            ("assistant", "An AI-authored reply that should never count as operator evidence."),
        ])])
    monkeypatch.setitem(adapter_registry._REGISTRY, AdapterType.SLACK_EXPORT, scan)
    result = service.ingest(source_id=source["source_id"])
    assert result["documents_new"] == 2
    assert result["observations_new"] > 0
    assert service.conn.execute("SELECT COUNT(DISTINCT document_id) FROM observations").fetchone()[0] == 1


def test_feedback_correct_precedence_over_new_observations(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))
    service.ingest(source_id=source["source_id"])

    corrected = service.fact_correct(subject_id=subject_id, facet="tone", new_value="always formal", reason="operator says so")
    fact_id = corrected["fact_id"]

    # A second ingest of contradictory-looking new evidence must never silently overwrite the
    # CORRECTED fact (build plan §4.2).
    more_root = _write_slack_notes(tmp_path, ["Casual notes, whatever, lol, nbd." for _ in range(3)])
    source2 = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(more_root))
    service.ingest(source_id=source2["source_id"])

    inspected = service.profile_inspect(subject_id=subject_id, facet="tone")
    tone_facts = [f for f in inspected["facts"] if f["facet"] == "tone"]
    assert len(tone_facts) == 1
    assert tone_facts[0]["value"] == "always formal"
    assert tone_facts[0]["state"] == "CORRECTED"


def test_feedback_add_classifies_and_asks_clarifying_question_when_ambiguous(service):
    setup = service.setup()
    result = service.feedback_add(subject_id=setup["subject_id"], utterance="banana")
    assert result["status"] == "clarifying_question"
    assert result["question"]


def test_erase_requires_confirmation_then_succeeds_with_exact_literal(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))
    service.ingest(source_id=source["source_id"])

    first = service.erase(scope="full_system")
    assert first["status"] == "confirmation_required"
    literal = _extract_literal(first["instructions"])

    with pytest.raises(ServiceError):  # wrong literal is rejected outright, never applies the erase
        service.erase(scope="full_system", challenge_literal="not-the-word")

    result = service.erase(scope="full_system", challenge_literal=literal)
    assert result["status"] == "erased"
    assert result["scope"] == "full_system"
    assert service.subject_list()["subjects"] == []


def test_export_requires_confirmation_and_writes_readable_file(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))
    service.ingest(source_id=source["source_id"])

    dest = tmp_path / "export.json"
    first = service.export(subject_id=subject_id, destination=str(dest))
    assert first["status"] == "confirmation_required"
    literal = _extract_literal(first["instructions"])

    result = service.export(subject_id=subject_id, destination=str(dest), challenge_literal=literal)
    assert result["status"] == "exported"
    assert dest.exists()
    payload = json.loads(dest.read_text())
    assert payload["facts"]


def test_off_refuses_mutations_but_allows_reads(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    off_result = service.off(reason="testing")
    assert off_result["status"] == "off"

    with pytest.raises(OperationRefused):
        service.subject_add(display_label="should be refused")

    # Reads keep working while off.
    status = service.status()
    assert status["off"] is True

    on_result = service.on()
    assert on_result["status"] == "on"
    service.subject_add(display_label="allowed now", kind="OPERATOR")


def test_draft_validate_flags_banned_phrase(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    service.fact_correct(subject_id=subject_id, facet="banned_phrase", new_value="delve", reason="don't say delve")
    result = service.draft_validate(subject_id=subject_id, draft_text="Let's delve into this topic.")
    assert result["outcome"] == "unvalidated"
    assert any(not c["passed"] for c in result["checks"])


def test_rebuild_and_reanalyze_never_downgrade_a_corrected_fact(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))
    ingest_result = service.ingest(source_id=source["source_id"])
    generation_id = ingest_result["generation_id"]

    service.fact_correct(subject_id=subject_id, facet="tone", new_value="my correction", reason="test")
    service.rebuild(generation_id=generation_id)
    inspected = service.profile_inspect(subject_id=subject_id, facet="tone")
    assert inspected["facts"][0]["value"] == "my correction"

    reanalyzed = service.reanalyze(subject_id=subject_id)
    assert reanalyzed["status"] == "reanalyzed"
    inspected2 = service.profile_inspect(subject_id=subject_id, facet="tone")
    assert inspected2["facts"][0]["value"] == "my correction"


def test_reanalyze_succeeds_from_persisted_observations_without_retained_source(service, tmp_path):
    """Build plan §13 M1 acceptance evidence bullet 10: "...exact rebuild, reanalysis failure
    without retained source... pass end to end." M1's zero-day-retention default (§5.1) means
    ingest() NEVER keeps raw document content (keep_content=False, hardcoded); reanalyze()
    recomputes fact synthesis purely from already-persisted Observation rows, so it must succeed
    honestly with no source content available at all -- proving zero-retention-by-default does
    not silently degrade or crash reanalysis."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))
    service.ingest(source_id=source["source_id"])

    # Confirm the retention guarantee actually held: no raw content column is populated for any
    # ingested document belonging to this subject.
    cur = service.conn.execute("SELECT content_enc FROM documents WHERE subject_id = ?", (subject_id,))
    rows = cur.fetchall()
    assert rows  # documents really were ingested
    assert all(row[0] is None for row in rows)

    result = service.reanalyze(subject_id=subject_id)
    assert result["status"] == "reanalyzed"
    assert result["facets"]  # real facets were reanalyzed purely from stored observations


def test_doctor_reports_schema_compatible_and_keychain_access(service):
    setup = service.setup()
    result = service.doctor()
    assert result["schema_compatible"] is True
    assert result["keychain_access"] is True
    assert result["backend_mode"] == "LOCAL_ONLY"
    assert result["backend_health"]


def _gmail_export_with_user_messages(tmp_path: Path, texts: list[str]) -> Path:
    from operator_voice.gmail.materialize import materialize
    root = tmp_path / "gmail-context"
    materialize(root, author_email="test@example.test", messages=[
        {"message_id": f"m{i}", "thread_id": f"t{i}", "from": "test@example.test",
         "internal_date": "2026-01-01T00:00:00Z", "text": text}
        for i, text in enumerate(texts)
    ])
    return root


def test_multi_context_synthesis_keeps_channels_as_distinct_facts(service, tmp_path):
    """§13 M2 worklist 1: calibrated MULTI-context intelligence. The same stylometric facet learned
    from two different channels must become two distinct scoped facts (general vs chat), never a
    single blended value."""
    setup = service.setup()
    subject_id = setup["subject_id"]

    slack_root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    service.ingest(source_id=service.source_add(
        subject_id=subject_id, adapter_type="slack_export", canonical_root=str(slack_root))["source_id"])

    chat_root = _gmail_export_with_user_messages(tmp_path, _SYNTHETIC_NOTES)
    service.ingest(source_id=service.source_add(
        subject_id=subject_id, adapter_type="gmail_export", canonical_root=str(chat_root))["source_id"])

    inspected = service.profile_inspect(subject_id=subject_id, facet="sentence_length")
    scopes = {f["scope"] for f in inspected["facts"]}
    # ContextScope.key() is positional "channel|audience|purpose|language|output_type"
    assert any(s.startswith("slack|") for s in scopes), scopes
    assert any(s.startswith("email|") for s in scopes), scopes
    # two distinct scoped facts for one facet — the whole point of multi-context voice
    assert len([f for f in inspected["facts"] if f["facet"] == "sentence_length"]) >= 2


def test_single_source_fact_raises_low_source_diversity_review(service, tmp_path):
    """§13 M2 worklist 3: a fact promoted on a single source is capped and surfaced in the operator
    review queue (deduplicated) rather than silently presented as fully corroborated."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)  # 3 docs, ONE source
    service.ingest(source_id=service.source_add(
        subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))["source_id"])

    reviews = service.review_list(subject_id=subject_id)
    reasons = {it["reason"] for it in reviews["items"]}
    assert "LOW_SOURCE_DIVERSITY" in reasons

    # de-dup: a second ingest of the same one-source condition must not spawn duplicate items
    before = reviews["count"]
    service.reanalyze(subject_id=subject_id)
    after = service.review_list(subject_id=subject_id)["count"]
    assert after == before

    # resolving an item removes it from the OPEN queue
    a_review = reviews["items"][0]
    resolved = service.review_resolve(review_id=a_review["review_id"], status="RESOLVED", resolution="ok")
    assert resolved["status"] == "resolved"
    assert service.review_list(subject_id=subject_id)["count"] == before - 1


def test_gateway_status_reports_both_egress_switches_disabled_by_default(service):
    service.setup()
    status = service.gateway_status()
    assert status["operator_egress_enabled"] is False
    assert status["external_egress_enabled"] is False
    assert status["runtime_gateway_enabled"] is False


def test_gateway_configure_enables_operator_egress_without_enabling_external(service):
    service.setup()
    status = service.gateway_configure(
        provider="local-deterministic", model="review-v0", allowed_data_classes=["operator"],
        enable_operator_egress=True,
    )
    assert status["operator_egress_enabled"] is True
    assert status["external_egress_enabled"] is False  # SEPARATE switch stayed off
    assert status["runtime_gateway_enabled"] is True
    # persisted + reflected on a fresh status read
    assert service.gateway_status()["provider"] == "local-deterministic"


def test_gateway_configure_rejects_raw_or_quarantined_in_allowlist(service):
    service.setup()
    with pytest.raises(ServiceError):
        service.gateway_configure(allowed_data_classes=["operator", "raw"], enable_operator_egress=True)
    with pytest.raises(ServiceError):
        service.gateway_configure(allowed_data_classes=["quarantined"], enable_operator_egress=True)


def test_gateway_configure_external_switch_is_explicit_and_separate(service):
    service.setup()
    status = service.gateway_configure(
        provider="local-deterministic", model="review-v0",
        allowed_data_classes=["operator", "external"],
        enable_operator_egress=True, enable_external_egress=True,
    )
    assert status["operator_egress_enabled"] is True
    assert status["external_egress_enabled"] is True


def test_gateway_calls_ledger_records_metadata_only_without_payload(service):
    """§4.4 zero payload retention: an egress attempt records provider/model/outcome metadata but
    NEVER the signal text."""
    service.setup()
    service.gateway_configure(
        provider="local-deterministic", model="review-v0", allowed_data_classes=["operator"],
        enable_operator_egress=True,
    )
    secret_signal = "PAYLOAD-MARKER-should-never-persist-42"
    from operator_voice.application.gateway import GatewayCallRequest

    result, _ = service._gateway_attempt(GatewayCallRequest(
        purpose="semantic_review", data_class="operator", provider="local-deterministic",
        model="review-v0", signal=secret_signal))
    assert result.outcome == "SUCCESS"

    ledger = service.gateway_calls()
    assert ledger["count"] == 1
    assert ledger["calls"][0]["outcome"] == "SUCCESS"
    # the raw signal must appear nowhere in the persisted ledger
    assert "PAYLOAD-MARKER" not in json.dumps(ledger)
    row = service.conn.execute("SELECT * FROM model_call_records").fetchone()
    assert "PAYLOAD-MARKER" not in json.dumps([row[k] for k in row.keys()])


def _service_with_judge(migrated_db, in_memory_crypto, fn):
    from operator_voice.application.gateway import CallableProvider

    conn, _ = migrated_db
    providers = {"judge": CallableProvider("judge", ("m",), fn)}
    svc = OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto, providers=providers)
    svc.setup()
    return svc


_CLEAN_DRAFT = "This is a perfectly ordinary draft. It says nothing that is off limits."


def test_semantic_requested_but_gateway_disabled_stays_deterministic_only(service):
    setup = service.setup()
    result = service.draft_validate(subject_id=setup["subject_id"], draft_text=_CLEAN_DRAFT, semantic=True)
    assert result["outcome"] == "deterministic_only"  # never silently 'validated'
    assert result["semantic_review"]["performed"] is True
    assert result["semantic_review"]["gateway_outcome"] == "DENIED_DISABLED"
    assert any(c["name"] == "semantic_fidelity" and not c["passed"] for c in result["checks"])


def test_semantic_validated_only_with_passing_configured_review(migrated_db, in_memory_crypto):
    svc = _service_with_judge(migrated_db, in_memory_crypto, lambda model, signal, purpose: "FIDELITY_OK")
    setup_subject = svc.subject_list()["subjects"][0]["subject_id"]
    svc.gateway_configure(provider="judge", model="m", allowed_data_classes=["operator"], enable_operator_egress=True)
    result = svc.draft_validate(subject_id=setup_subject, draft_text=_CLEAN_DRAFT, semantic=True)
    assert result["outcome"] == "validated"
    assert result["semantic_review"]["verdict"] is True


def test_semantic_material_change_yields_unvalidated(migrated_db, in_memory_crypto):
    svc = _service_with_judge(migrated_db, in_memory_crypto, lambda model, signal, purpose: "MATERIAL_CHANGE")
    subject_id = svc.subject_list()["subjects"][0]["subject_id"]
    svc.gateway_configure(provider="judge", model="m", allowed_data_classes=["operator"], enable_operator_egress=True)
    result = svc.draft_validate(subject_id=subject_id, draft_text=_CLEAN_DRAFT, semantic=True)
    assert result["outcome"] == "unvalidated"
    assert result["semantic_review"]["verdict"] is False


def test_semantic_provider_outage_degrades_to_deterministic_only(migrated_db, in_memory_crypto):
    def _boom(model, signal, purpose):
        raise RuntimeError("provider outage")

    svc = _service_with_judge(migrated_db, in_memory_crypto, _boom)
    subject_id = svc.subject_list()["subjects"][0]["subject_id"]
    svc.gateway_configure(provider="judge", model="m", allowed_data_classes=["operator"], enable_operator_egress=True)
    result = svc.draft_validate(subject_id=subject_id, draft_text=_CLEAN_DRAFT, semantic=True)
    # provider outage leaves deterministic operation healthy and honestly labeled (§13 acceptance)
    assert result["outcome"] == "deterministic_only"
    assert result["semantic_review"]["gateway_outcome"] == "PROVIDER_ERROR"


def test_injected_forbidden_fact_is_unvalidated_and_never_egresses(migrated_db, in_memory_crypto):
    calls = {"n": 0}

    def _count(model, signal, purpose):
        calls["n"] += 1
        return "FIDELITY_OK"

    svc = _service_with_judge(migrated_db, in_memory_crypto, _count)
    subject_id = svc.subject_list()["subjects"][0]["subject_id"]
    svc.gateway_configure(provider="judge", model="m", allowed_data_classes=["operator"], enable_operator_egress=True)
    result = svc.draft_validate(
        subject_id=subject_id, draft_text="Our product is FDA approved.",
        forbidden_facts=["FDA approved"], semantic=True,
    )
    assert result["outcome"] == "unvalidated"  # a forbidden/injected claim can never be validated
    assert result["semantic_review"]["performed"] is False  # deterministic failure skips egress
    assert calls["n"] == 0  # the draft never reached the provider
    assert svc.gateway_calls()["count"] == 0


def test_gateway_is_disabled_by_default_and_visible_in_status_and_doctor(service):
    """Build plan §13 M1 acceptance evidence bullet 5: "...and gateway-off behavior are verified
    in a disposable home." A service constructed with no explicit gateway argument (exactly how
    every M1 CLI/MCP entrypoint constructs it via runtime.bootstrap.open_runtime) gets the shared
    disabled instance, and that fact is honestly surfaced in both status() and doctor()."""
    assert service.gateway.enabled is False
    service.setup()
    assert service.status()["gateway_enabled"] is False
    assert service.doctor()["gateway_enabled"] is False

    from operator_voice.application.gateway import GatewayCallRequest, GatewayDisabledError

    with pytest.raises(GatewayDisabledError):
        service.gateway.call(GatewayCallRequest(purpose="x", data_class="operator", provider="none", model="none", signal="x"))


def _slack_subject(service, tmp_path):
    subject_id = service.setup()["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    service.ingest(source_id=service.source_add(
        subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))["source_id"])
    return subject_id, root


def test_reextract_previews_requires_challenge_backs_up_and_stamps_a_generation(service, tmp_path):
    subject_id, root = _slack_subject(service, tmp_path)
    before = service.profile_inspect(subject_id=subject_id)["latest_generation_id"]
    sorted(root.glob("*.md"))[0].unlink()
    preview = service.reextract(subject_id=subject_id, dry_run=True)
    assert preview["status"] == "preview" and preview["sources"][0]["dropped_file_missing"] == 1
    challenge = service.reextract(subject_id=subject_id)
    assert challenge["status"] == "confirmation_required"
    done = service.reextract(subject_id=subject_id, challenge_literal=_extract_literal(challenge["instructions"]))
    assert Path(done["backup_path"]).exists() and done["generation_id"] not in (None, before)
    assert service.profile_inspect(subject_id=subject_id)["latest_generation_id"] == done["generation_id"]


def test_reextract_refuses_when_a_source_folder_is_emptied(service, tmp_path):
    from operator_voice.application.service import ServiceError
    subject_id, root = _slack_subject(service, tmp_path)
    for p in list(root.iterdir()):
        p.unlink()
    with pytest.raises(ServiceError):
        service.reextract(subject_id=subject_id, dry_run=True)


def test_reanalyze_twice_leaves_one_live_fact_per_facet_and_scope(service, tmp_path):
    subject_id, _ = _slack_subject(service, tmp_path)
    service.reanalyze(subject_id=subject_id)
    service.reanalyze(subject_id=subject_id)
    keys = [(f["facet"], f["scope"]) for f in service.profile_inspect(subject_id=subject_id)["facts"]]
    assert len(keys) == len(set(keys))


def test_reextract_drops_slides_and_unregistered_docs_and_retires_their_facts(service, tmp_path, monkeypatch):
    from operator_voice.drive.materialize import _format_block
    from operator_voice.adapters.drive_export import parse_export_file
    sid = service.setup()["subject_id"]
    root = tmp_path / "drive"
    root.mkdir()
    for native, mime in [("doc", "document"), ("slides", "presentation"), ("outside", "document")]:
        (root / f"2026__{native}__r.md").write_text(_format_block(
            native, "r", "2026-01-01T00:00:00Z", f"application/vnd.google-apps.{mime}",
            "Synthetic", _SYNTHETIC_NOTES[0] + f" Synthetic {native} example."))
    (root / ".folder-membership.json").write_text(json.dumps({
        "doc": {"folder_id": "writing", "via_shortcut": False},
        "outside": {"folder_id": "other", "via_shortcut": False},
    }))
    source_id = service.source_add(subject_id=sid, adapter_type="drive_export", canonical_root=str(root))["source_id"]
    from operator_voice.persistence import store
    store.set_source_role_identity(service.conn, source_id, {"subject_role": "user", "registered_folders": ["writing"]})
    def pre_filter(reg, cursor, limit=None):
        candidates = []
        for path in sorted(root.glob("*.md")):
            record = parse_export_file(path.read_text())[0]
            slides = record["mime_type"].endswith("presentation")
            c = make_candidate(subject_id=sid, source_id=source_id, source_native_id=record["file_id"],
                               role="user", raw_text=record["body"], locator=f"drive:{record['file_id']}:r",
                               channel="slides" if slides else "docs", output_type="slide" if slides else "doc")
            c.context = {"modified_time": record["modified_time"], "revision_id": "r"}
            candidates.append(c)
        return AdapterScanResult(candidates=candidates, next_cursor=None)
    with monkeypatch.context() as patch:
        patch.setitem(adapter_registry._REGISTRY, AdapterType.DRIVE_EXPORT, pre_filter)
        service.ingest(source_id=source_id)
    assert any(f["scope"].startswith("slides|") for f in service.profile_inspect(subject_id=sid)["facts"])
    challenge = service.reextract(subject_id=sid)
    done = service.reextract(subject_id=sid, challenge_literal=_extract_literal(challenge["instructions"]))
    assert done["sources"][0]["dropped_now_filtered"] == 2
    assert service.conn.execute("SELECT COUNT(DISTINCT document_id) FROM observations").fetchone()[0] == 1
    assert not any(f["scope"].startswith("slides|") for f in service.profile_inspect(subject_id=sid)["facts"])


def test_ingest_stores_word_count_without_pasted_log_lines(service, tmp_path):
    from operator_voice.persistence import store
    sid = service.setup()["subject_id"]
    text = "We should ship this soon.\n2026-01-01 12:00:00 INFO synthetic diagnostic line\nSounds good."
    root = write_slack_notes(tmp_path / "word-count", [text])
    source = service.source_add(subject_id=sid, adapter_type="slack_export", canonical_root=str(root))
    service.ingest(source_id=source["source_id"])
    row = service.conn.execute("SELECT document_id,context_json FROM documents").fetchone()
    assert json.loads(row["context_json"])["words"] == 7
    assert store.get_document(service.conn, service.crypto, row["document_id"]).words == 7


def test_repeated_documents_use_integration_specific_keepers(service, tmp_path, monkeypatch):
    from operator_voice.domain.models import AuthorshipState
    sid = service.setup()['subject_id']
    duplicate = 'A synthetic message with enough words to learn from. We should ship soon.'
    for adapter, natives, revisions in [
        ('gmail_export', ['a', 'b', 'c'], [None] * 3),
        ('slack_export', ['z:9.0', 'a:10.0'], [None] * 2),
        ('drive_export', ['z', 'a'], ['2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z']),
    ]:
        root = tmp_path / adapter
        root.mkdir()
        source_id = service.source_add(subject_id=sid, adapter_type=adapter, canonical_root=str(root))['source_id']
        source = store.get_source(service.conn, service.crypto, source_id)
        for i, (native, revision) in enumerate(zip(natives, revisions)):
            text = duplicate if i == 0 else duplicate.upper().replace(' ', '  ')
            if i == 2:
                text = 'A distinct synthetic email for the plan. Please send feedback today.'
            candidate = make_candidate(subject_id=sid, source_id=source_id, source_native_id=native,
                                       role='user', raw_text=text, locator='synthetic', channel='email' if adapter == 'gmail_export' else 'slack')
            candidate.context = {'modified_time': revision} if revision else {}
            service._extract_document(service.conn, source, candidate, 'user')
    repeated = store.repeated_document_ids(service.conn, sid)
    rows = service.conn.execute('SELECT document_id,source_native_id,context_json FROM documents').fetchall()
    assert {r['source_native_id'] for r in rows if r['document_id'] in repeated} == {'a', 'b', 'a:10.0'}
    assert len(repeated) == 4  # Gmail a+b, Slack later, Drive later a
    assert all(json.loads(r['context_json'])['text_key'] for r in rows)
    captured = []
    from operator_voice.application import service as module
    original = module.synthesize_fact
    def observe(**kwargs):
        captured.extend(o.document_id for o in kwargs['observations'])
        return original(**kwargs)
    monkeypatch.setattr(module, 'synthesize_fact', observe)
    service._synthesize_and_review(service.conn, sid, set(store.list_facets_for_subject(service.conn, sid)), protect_only_corrected=True)
    assert captured and not (set(captured) & repeated)


def test_reextract_gmail_preview_trim_repeats_and_metadata(service, tmp_path, monkeypatch):
    from operator_voice.persistence import backup
    from operator_voice.gmail import materialize
    from operator_voice.domain.models import AdapterType
    sid = service.setup()['subject_id']
    root = tmp_path / 'gmail'
    root.mkdir()
    source_id = service.source_add(subject_id=sid, adapter_type='gmail_export', canonical_root=str(root))['source_id']
    def write(native, body):
        path = root / f'2026-01-01T00-00-00Z__{native}.md'
        path.write_text(f'<!-- ovt:gmail id={native} thread=t internal=2026-01-01T00:00:00Z -->\n## Old subject\n\n{body}\n')
        return path
    reply = 'This is my own short reply to the request.'
    quoted = reply + '\nOn Monday,\nSynthetic Sender wrote:\nUntrusted synthetic quoted text.'
    a = write('a', quoted)
    write('b', reply.upper())
    write('c', 'A separate synthetic message.\nFrom: Synthetic Sender\nSent: Monday\nKeep this text for manual review.')
    empty = write('empty', 'On Monday,\nSynthetic Sender wrote:\nTheir synthetic message.')
    # Model an old install that stored every record before the new filters/metadata.
    source = store.get_source(service.conn, service.crypto, source_id)
    for path in root.glob('*.md'):
        r = materialize.parse_export_file(path.read_text())[0]
        candidate = make_candidate(subject_id=sid, source_id=source_id, source_native_id=r['message_id'], role='user', raw_text=r['body'], locator=f"gmail:t:{r['message_id']}", channel='email')
        service._extract_document(service.conn, source, candidate, 'user')
    service.conn.execute("UPDATE documents SET context_json=json_remove(context_json,'$.words','$.text_key')")
    before = a.read_bytes()
    preview = service.reextract(subject_id=sid, dry_run=True)
    assert preview['gmail_files_trimmed'] == 2
    assert preview['repeated_documents'] == 2
    assert preview['header_shaped'] == 1
    assert preview['sources'][0]['filters']['no_own_text'] == 1
    assert preview['sources'][0]['dropped_file_missing'] == 0
    assert a.read_bytes() == before
    original_backup = backup.create_backup
    backed_up = []
    def verify_order(*args):
        assert a.read_bytes() == before
        backed_up.append(True)
        return original_backup(*args)
    monkeypatch.setattr(backup, 'create_backup', verify_order)
    challenge = service.reextract(subject_id=sid)
    done = service.reextract(subject_id=sid, challenge_literal=_extract_literal(challenge['instructions']))
    assert backed_up
    assert done['repeated_documents'] == preview['repeated_documents']
    assert done['gmail_files_trimmed'] == preview['gmail_files_trimmed']
    assert done['sources'][0]['filters']['no_own_text'] == 1
    assert done['sources'][0]['dropped_file_missing'] == 0
    assert materialize.parse_export_file(empty.read_text())[0]['body'] == ''
    assert 'wrote:' not in a.read_text()
    rows = service.conn.execute('SELECT context_json FROM documents').fetchall()
    assert len(rows) == 1
    assert all(json.loads(r[0])['words'] > 0 and json.loads(r[0])['text_key'] for r in rows)
    monkeypatch.setattr(backup, 'create_backup', original_backup)
    challenge = service.reextract(subject_id=sid)
    second = service.reextract(subject_id=sid, challenge_literal=_extract_literal(challenge['instructions']))
    assert second['sources'][0]['reextracted'] == second['gmail_files_trimmed'] == 0


def test_new_repeated_gmail_copy_retires_the_previously_learned_facts(service, tmp_path):
    sid = service.setup()['subject_id']
    root = tmp_path / 'mail'
    root.mkdir()
    source_id = service.source_add(subject_id=sid, adapter_type='gmail_export', canonical_root=str(root))['source_id']
    source = store.get_source(service.conn, service.crypto, source_id)
    for native in ['a', 'b']:
        candidate = make_candidate(subject_id=sid, source_id=source_id, source_native_id=native,
                                   role='user', raw_text='Please review the synthetic plan today. We will publish soon.',
                                   locator='synthetic', channel='email')
        service._extract_document(service.conn, source, candidate, 'user')
        service._synthesize_and_review(service.conn, sid, set(store.list_facets_for_subject(service.conn, sid)), protect_only_corrected=True)
        if native == 'a':
            assert store.list_facts(service.conn, service.crypto, sid)
    assert store.list_facts(service.conn, service.crypto, sid) == []


def test_status_reextract_notice_clears_after_current_rebuild(service, tmp_path):
    sid, _ = _slack_subject(service, tmp_path)
    assert 'operator-voice reextract' in service.status()['brief']
    challenge = service.reextract(subject_id=sid)
    service.reextract(subject_id=sid, challenge_literal=_extract_literal(challenge['instructions']))
    assert 'operator-voice reextract' not in service.status()['brief']


@pytest.mark.parametrize('integration,channel', [('slack', 'slack'), ('gmail', 'email'), ('drive', 'docs')])
def test_daily_policy_retires_weekly_offers_and_preserves_legacy_audit(service, tmp_path, monkeypatch, integration, channel):
    from operator_voice.runtime.paths import RuntimePaths
    from operator_voice.application import service as module
    from importlib import import_module
    from operator_voice.persistence import audit
    materialize = import_module(f'operator_voice.{integration}.materialize')
    service.paths = RuntimePaths(config_dir=tmp_path/'config', data_dir=tmp_path/'data', state_dir=tmp_path/'state')
    service.paths.ensure()
    sid = service.setup()['subject_id']
    export_dir = getattr(service.paths, f'{integration}_export_dir')
    now = module.now_ts()
    materialize.write_import_state(export_dir, {'last_import_at': now - 8 * 86400})
    def warnings():
        return service.context_get(subject_id=sid, consumer_id='synthetic-test', channel=channel)['warnings']
    assert not any(w.startswith('refresh_offer_due:') for w in warnings())
    service.refresh_offer(integration=integration, answer='no')
    assert audit.latest(service.conn, f'refresh.offer:{integration}').result == 'no'
    assert f'refresh_offer_due:{integration}' not in warnings()
    monkeypatch.setattr(module, 'now_ts', lambda: now + 8 * 86400)
    assert not any(w.startswith('refresh_offer_due:') for w in warnings())
    assert getattr(service, f'{integration}_status')()['stale'] is True
    materialize.write_import_state(export_dir, {'last_import_at': now + 7 * 86400})
    assert f'refresh_offer_due:{integration}' not in warnings()
    assert getattr(service, f'{integration}_status')()['stale'] is False
    assert not any(w.startswith('refresh_offer_due:') for w in service.context_get(subject_id=sid, consumer_id='synthetic-test', channel='general')['warnings'])


def test_refresh_offer_never_scans_evidence_or_blocks_context(service, tmp_path, monkeypatch):
    from operator_voice.runtime.paths import RuntimePaths
    from operator_voice.slack.materialize import write_import_state
    from operator_voice.domain.models import now_ts
    service.paths = RuntimePaths(config_dir=tmp_path/'config', data_dir=tmp_path/'data', state_dir=tmp_path/'state')
    service.paths.ensure()
    sid = service.setup()['subject_id']
    write_import_state(service.paths.slack_export_dir, {'last_import_at': now_ts() - 8 * 86400})
    def fail(*args, **kwargs):
        raise ServiceError('synthetic unavailable evidence root')
    monkeypatch.setattr(service, '_coverage', fail)
    payload = service.context_get(subject_id=sid, consumer_id='synthetic-test', channel='slack')
    assert 'refresh_offer_due:slack' not in payload['warnings']
