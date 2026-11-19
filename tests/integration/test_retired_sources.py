"""Upgrade safety for retired sources; synthetic evidence only."""
import re

import pytest

from operator_voice.application.service import OperatorVoiceService, ServiceError
from operator_voice.domain.models import (
    AdapterType, RETIRED_ADAPTER_TYPES, SourceRegistration, now_ts,
)
from operator_voice.persistence import store
from synthetic_sources import write_slack_notes


@pytest.fixture
def service(migrated_db, in_memory_crypto):
    return OperatorVoiceService(migrated_db[0], in_memory_crypto,
                                profile_pack_crypto=in_memory_crypto)


def _legacy(service, tmp_path):
    sid = service.setup()["subject_id"]
    source = SourceRegistration(
        source_id="src_retired", subject_id=sid, adapter_type=AdapterType.CURSOR_TRANSCRIPT,
        canonical_root=str(tmp_path / "never-read"), opted_in_at=now_ts(),
        approved_purpose="voice_generation", active=True, cursor="unchanged",
        role_identity={"subject_role": "user"},
    )
    store.insert_source(service.conn, service.crypto, source)
    service.conn.commit()
    return source


def test_retired_source_curate_skips_without_scan_failure_or_cursor_change(service, tmp_path, monkeypatch):
    from operator_voice.adapters import registry
    source = _legacy(service, tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("retired source was read")
    monkeypatch.setitem(registry._REGISTRY, source.adapter_type, forbidden)
    result = service.curate()
    assert result["retired_sources"] == 1
    assert result["sources_failed"] == 0
    assert store.get_source_curation_state(service.conn, source.source_id) is None
    assert store.get_source(service.conn, service.crypto, source.source_id).cursor == "unchanged"
    with pytest.raises(ServiceError, match="erase --scope source"):
        service.ingest(source_id=source.source_id)


@pytest.mark.parametrize("adapter", sorted(RETIRED_ADAPTER_TYPES, key=lambda a: a.value))
def test_retired_source_add_refused(service, tmp_path, adapter):
    sid = service.setup()["subject_id"]
    with pytest.raises(ServiceError, match="Agent chats and local notes are no longer used"):
        service.source_add(subject_id=sid, adapter_type=adapter.value, canonical_root=str(tmp_path))


def test_retired_observations_never_change_resynthesis(service, tmp_path):
    sid = service.setup()["subject_id"]
    good = write_slack_notes(tmp_path / "good", [
        "We should ship this change soon. The team agrees with the plan.",
        "We can finish the work today. It is ready for the next step.",
        "We have enough evidence here. The results support our decision.",
    ])
    active = service.source_add(subject_id=sid, adapter_type="slack_export", canonical_root=str(good))
    service.ingest(source_id=active["source_id"])
    before = {f.facet: f.value for f in store.list_facts(service.conn, service.crypto, sid)}
    old = write_slack_notes(tmp_path / "legacy", ["One giant sentence " * 80 for _ in range(3)])
    retired = service.source_add(subject_id=sid, adapter_type="slack_export", canonical_root=str(old))
    service.ingest(source_id=retired["source_id"])
    service.conn.execute("UPDATE sources SET adapter_type = 'cursor_transcript' WHERE source_id = ?",
                         (retired["source_id"],))
    service.conn.commit()
    service.reanalyze(subject_id=sid)
    after = {f.facet: f.value for f in store.list_facts(service.conn, service.crypto, sid)}
    assert after == before
    assert store.list_document_ids_for_source(service.conn, retired["source_id"])


def test_retired_notice_lasts_until_source_erase(service, tmp_path):
    from operator_voice.domain.models import SourceDocument, AuthorshipState
    source = _legacy(service, tmp_path)
    assert "retired sources" not in service.status()["brief"]
    store.upsert_document(service.conn, service.crypto, SourceDocument(
        document_id="doc_legacy", subject_id=source.subject_id, source_id=source.source_id,
        source_native_id="legacy", digest="synthetic", locator="synthetic", content="",
        authorship_state=AuthorshipState.SUBJECT,
    ), keep_content=False)
    service.conn.commit()
    assert "1 retired sources" in service.status()["brief"]
    from operator_voice.domain.models import SourceCurationState
    store.upsert_source_curation_state(service.conn, SourceCurationState(source_id=source.source_id))
    challenge = service.erase(scope="source", target=source.source_id)
    literal = re.search(r"challenge_literal='([^']+)'", challenge["instructions"]).group(1)
    service.erase(scope="source", target=source.source_id, challenge_literal=literal)
    assert "retired sources" not in service.status()["brief"]


def test_full_erase_removes_curation_tables(service, tmp_path):
    sid = service.setup()["subject_id"]
    root = write_slack_notes(tmp_path / "notes", ["A synthetic note long enough for curation."])
    service.source_add(subject_id=sid, adapter_type="slack_export", canonical_root=str(root))
    service.curate()
    assert service.conn.execute("SELECT COUNT(*) FROM source_curation_state").fetchone()[0] == 1
    assert service.conn.execute("SELECT COUNT(*) FROM curation_runs").fetchone()[0] == 1
    challenge = service.erase(scope="full_system")
    literal = re.search(r"challenge_literal='([^']+)'", challenge["instructions"]).group(1)
    service.erase(scope="full_system", challenge_literal=literal)
    assert service.conn.execute("SELECT COUNT(*) FROM source_curation_state").fetchone()[0] == 0
    assert service.conn.execute("SELECT COUNT(*) FROM curation_runs").fetchone()[0] == 0
