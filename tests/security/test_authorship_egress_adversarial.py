"""M2 adversarial security/privacy suite (build plan §4.4 threat model, §9 authorship/contamination,
§13 M2 acceptance). Two boundaries under attack, with SYNTHETIC adversarial fixtures only:

1. Authorship/quarantine smuggling: third-party / AI / quoted / forwarded / ambiguous text is pushed
   into an operator source and must contribute ZERO operator evidence. The strong assertion is a
   differential one — the operator's stylometric fact value is byte-identical whether or not the
   smuggled block is present, so the attacker's text provably changed nothing.
2. Separate default-deny EXTERNAL egress: enabling operator model egress must NEVER enable EXTERNAL
   egress, and an EXTERNAL subject must never receive a VoiceContextBundle or authoring instructions.
"""

from __future__ import annotations

from synthetic_sources import write_slack_notes

import json
from pathlib import Path

import pytest

from operator_voice.application.gateway import GatewayCallRequest
from operator_voice.application.service import OperatorVoiceService, ServiceError

# Short operator sentences: mean words/sentence ~1.5. A smuggled long sentence (~40 words) would
# drastically inflate this metric IF it were ever wrongly counted.
_SUBJECT_NOTES = [
    "Yes.\nOk. Sure. Fine.",
    "Right. Sounds good. Agreed here.",
    "Sure thing. Will do. Thanks all.",
]
_SMUGGLED_SENTENCE = (
    "This is an extremely long smuggled sentence authored by a third party that deliberately "
    "packs many many words into a single uninterrupted run on clause specifically so that it "
    "would badly distort the operator average sentence length metric if it were ever counted."
)


@pytest.fixture
def service(migrated_db, in_memory_crypto):
    conn, _ = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


def _new_service(migrated_db, in_memory_crypto):
    conn, _ = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


_counter = {"n": 0}


def _ingest_slack_notes(svc: OperatorVoiceService, tmp_path: Path, notes: list[str]) -> dict:
    _counter["n"] += 1
    root = tmp_path / f"drop-{_counter['n']}"
    root.mkdir()
    write_slack_notes(root, notes, conversations=len(notes))
    setup = svc.setup()
    source = svc.source_add(subject_id=setup["subject_id"], adapter_type="slack_export", canonical_root=str(root))
    result = svc.ingest(source_id=source["source_id"])
    result["_subject_id"] = setup["subject_id"]
    return result


def _sentence_length_value(svc: OperatorVoiceService, subject_id: str) -> float | None:
    facts = svc.profile_inspect(subject_id=subject_id)["facts"]
    vals = [f["value"] for f in facts if f["facet"] == "sentence_length"]
    return float(vals[0]) if vals else None


# ---------------------------------------------------------------------------------------------
# 1. Authorship / quarantine smuggling — differential "changed nothing" proof
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "smuggle",
    [
        pytest.param(lambda s: f"{s}\n\n-----Original Message-----\nFrom: Eve Adversary\n\n{_SMUGGLED_SENTENCE}", id="forwarded_header"),
        pytest.param(lambda s: f"{s}\n\nOn Jan 1, 2020, Eve Adversary wrote:\n{_SMUGGLED_SENTENCE}", id="on_date_wrote"),
        pytest.param(lambda s: f"{s}\n\n> {_SMUGGLED_SENTENCE}", id="gt_quoted_block"),
    ],
)
def test_quoted_or_forwarded_text_changes_no_operator_evidence(smuggle, migrated_db, in_memory_crypto, tmp_path):
    # Control: clean operator notes only.
    clean_svc = _new_service(migrated_db, in_memory_crypto)
    clean = _ingest_slack_notes(clean_svc, tmp_path, _SUBJECT_NOTES)
    clean_val = _sentence_length_value(clean_svc, clean["_subject_id"])
    assert clean_val is not None and clean_val < 3.0  # short operator sentences

    # Attack: same notes with a long third-party block smuggled into each.
    from operator_voice.persistence.sqlite import connect
    from operator_voice.persistence import migrations

    db2 = tmp_path / "attack.sqlite3"
    conn2 = connect(db2)
    migrations.migrate(conn2)
    attack_svc = OperatorVoiceService(conn2, in_memory_crypto, profile_pack_crypto=in_memory_crypto)
    smuggled_notes = [smuggle(n) for n in _SUBJECT_NOTES]
    attack = _ingest_slack_notes(attack_svc, tmp_path, smuggled_notes)
    attack_val = _sentence_length_value(attack_svc, attack["_subject_id"])

    # Outer message is operator-authored, so it is NOT quarantined ...
    assert attack["documents_quarantined"] == 0
    assert attack["observations_new"] > 0
    # ... but the smuggled block contributed ZERO evidence: the metric is identical to the control.
    assert attack_val is not None
    assert abs(attack_val - clean_val) < 1e-9, (
        f"smuggled third-party text changed operator evidence: clean={clean_val} attack={attack_val}"
    )
    assert attack_val < 3.0  # a counted 40-word sentence would have blown past this
    conn2.close()


def _ingest_adversarial_candidates(service, tmp_path, monkeypatch, turns):
    from operator_voice.adapters import registry
    from operator_voice.adapters.base import AdapterScanResult, make_candidate
    from operator_voice.domain.models import AdapterType
    setup = service.setup()
    source = service.source_add(subject_id=setup["subject_id"], adapter_type="slack_export",
                                canonical_root=str(tmp_path))
    def scan(reg, cursor, limit=None):
        return AdapterScanResult(next_cursor=None, candidates=[make_candidate(
            subject_id=setup["subject_id"], source_id=reg.source_id, source_native_id=str(i),
            role=role, raw_text=text, locator=f"slack:synthetic:{i}", channel="slack",
        ) for i, (role, text) in enumerate(turns)])
    monkeypatch.setitem(registry._REGISTRY, AdapterType.SLACK_EXPORT, scan)
    return setup["subject_id"], service.ingest(source_id=source["source_id"])


def test_assistant_candidates_contribute_zero_evidence(service, tmp_path, monkeypatch):
    """The shared SUBJECT gate must reject AI roles even if a retained scanner emits one."""
    sid, result = _ingest_adversarial_candidates(service, tmp_path, monkeypatch,
        [(role, text) for _ in range(3) for role, text in
         [("user", "Yes."), ("assistant", _SMUGGLED_SENTENCE)]])
    assert result["documents_new"] == 6
    val = _sentence_length_value(service, sid)
    if val is not None:
        assert val < 3.0
    assert service.conn.execute("SELECT COUNT(DISTINCT document_id) FROM observations").fetchone()[0] == 3


def test_unknown_role_is_quarantined_and_yields_zero_evidence(service, tmp_path, monkeypatch):
    sid, result = _ingest_adversarial_candidates(service, tmp_path, monkeypatch,
        [("mystery_impersonator", _SMUGGLED_SENTENCE)])
    assert result["documents_quarantined"] >= 1
    assert result["observations_new"] == 0
    assert _sentence_length_value(service, sid) is None


# ---------------------------------------------------------------------------------------------
# 2. Separate default-deny EXTERNAL egress
# ---------------------------------------------------------------------------------------------


def test_enabling_operator_egress_leaves_external_egress_denied_end_to_end(service):
    """§D17 acceptance: 'enabling operator egress leaves EXTERNAL egress denied'. Configure a working
    provider with operator egress ON and external egress OFF, then attempt an EXTERNAL-derived
    signal — it must be denied by the SEPARATE switch even though the provider/model/data-class and
    operator egress all check out."""
    from operator_voice.application.gateway import CallableProvider

    svc = service
    svc.setup()
    # Inject a provider and enable ONLY operator egress.
    svc._providers = {"judge": CallableProvider("judge", ("m",), lambda model, signal, purpose: "FIDELITY_OK")}
    svc.gateway_configure(provider="judge", model="m", allowed_data_classes=["operator", "external"],
                          enable_operator_egress=True, enable_external_egress=False)
    status = svc.gateway_status()
    assert status["operator_egress_enabled"] is True
    assert status["external_egress_enabled"] is False

    # An EXTERNAL-derived signal is denied by the separate switch ...
    result, _decision = svc._gateway_attempt(GatewayCallRequest(
        purpose="semantic_review", data_class="external", provider="judge", model="m",
        signal="x", is_external=True,
    ))
    assert result.outcome == "DENIED_EXTERNAL_EGRESS"
    assert result.allowed is False
    assert result.text is None  # nothing ever reached the provider

    # ... while an operator-derived signal on the same config is allowed (proves the denial is the
    # external switch, not a broken gateway).
    ok, _ = svc._gateway_attempt(GatewayCallRequest(
        purpose="semantic_review", data_class="operator", provider="judge", model="m",
        signal="x", is_external=False,
    ))
    assert ok.allowed is True


def test_external_subject_gets_no_bundle_and_no_authoring_instructions(service):
    """An EXTERNAL subject can never receive a VoiceContextBundle or generation/authoring
    instructions (§4.3, D17). context.get refuses outright; subject.insights returns an
    analysis-only bundle that structurally carries neither instructions nor ranked_facts."""
    service.setup()
    external = service.subject_add(display_label="Some External Person", kind="EXTERNAL", purposes=["analysis"])
    ext_id = external["subject_id"]

    with pytest.raises(ServiceError):
        service.context_get(subject_id=ext_id, consumer_id="attacker-consumer")

    insight = service.subject_insights(subject_id=ext_id, purpose="analysis")
    assert insight["schema"].startswith("subject-insight-bundle/")
    assert "instructions" not in insight
    assert "ranked_facts" not in insight
    # and it validates under the contract that hard-rejects those keys
    from operator_voice.domain.contracts import validate_subject_insight_bundle

    validate_subject_insight_bundle(insight)
