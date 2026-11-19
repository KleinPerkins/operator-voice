"""M2 subject-resolution guardrails from the frozen bootstrap plan and ADR 0003."""

from __future__ import annotations

import ast
import inspect
import json

import pytest

from operator_voice.application import service as service_module
from operator_voice.application.service import OperatorVoiceService, ServiceError
from operator_voice.domain.help import error_taxonomy
from operator_voice.domain.operations import get_operation


@pytest.fixture
def service(migrated_db, in_memory_crypto):
    conn, _db_path = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


def test_unknown_subject_error_is_actionable(service):
    helper = getattr(service_module, "_unknown_subject_error")
    detail = str(helper("KleinPerkins"))

    assert "unknown subject_id" in detail
    assert "opaque" in detail and "subj_*" in detail
    assert all(term in detail for term in ("name", "handle", "email"))
    assert "subject-list" in detail
    assert "consumer_id" not in detail
    assert "--consumer-id" not in detail

    with pytest.raises(ServiceError) as context_error:
        service.context_get(subject_id="KleinPerkins", consumer_id="host-agent")
    assert "unknown subject_id" in str(context_error.value)
    assert "subject-list" in str(context_error.value)
    assert "consumer_id" in str(context_error.value)
    assert "--consumer-id" in str(context_error.value)

    with pytest.raises(ServiceError) as insights_error:
        service.subject_insights(
            subject_id="KleinPerkins", purpose="communication_pattern_analysis"
        )
    assert "unknown subject_id" in str(insights_error.value)
    assert "subject-list" in str(insights_error.value)
    assert "consumer_id" not in str(insights_error.value)
    assert "--consumer-id" not in str(insights_error.value)

    tree = ast.parse(inspect.getsource(service_module))
    helper_raises = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "_unknown_subject_error"
    ]
    consumer_guided_raises = [
        node
        for node in helper_raises
        if any(
            keyword.arg == "needs_consumer_id"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.exc.keywords
        )
    ]
    bare_raises = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and node.exc is not None
        and any(
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and "unknown subject_id" in part.value
            for part in ast.walk(node.exc)
        )
    ]
    assert len(helper_raises) == 10
    assert len(consumer_guided_raises) == 1
    assert bare_raises == []


def test_missing_consumer_id_is_actionable(service):
    subject_id = service.setup()["subject_id"]

    with pytest.raises(ServiceError) as missing:
        service.context_get(subject_id=subject_id)
    with pytest.raises(ServiceError) as empty:
        service.context_get(subject_id=subject_id, consumer_id="")

    for detail in (str(missing.value), str(empty.value)):
        assert "consumer_id" in detail
        assert "--consumer-id" in detail
        assert "agent/tool" in detail


def test_context_get_defaults_to_sole_operator_subject(service):
    subject_id = service.setup(display_label="Operator label must stay private")["subject_id"]
    service.subject_add(
        display_label="External label must not affect resolution",
        kind="EXTERNAL",
        purposes=["communication_pattern_analysis"],
    )

    operation = get_operation("context.get")
    args = {arg.name: arg for arg in operation.args}
    assert args["subject_id"].required is False
    assert args["consumer_id"].required is True

    defaulted = service.context_get(consumer_id="host-agent")
    explicit = service.context_get(subject_id=subject_id, consumer_id="host-agent")
    empty = service.context_get(subject_id="", consumer_id="host-agent")
    whitespace = service.context_get(subject_id="   ", consumer_id="host-agent")
    stripped = service.context_get(subject_id=f"  {subject_id}  ", consumer_id="host-agent")
    assert defaulted["subject_id"] == subject_id
    assert explicit["subject_id"] == subject_id
    assert empty["subject_id"] == subject_id
    assert whitespace["subject_id"] == subject_id
    assert stripped["subject_id"] == subject_id


def test_context_get_refuses_when_multiple_subjects(service):
    first_id = service.setup(display_label="First private label")["subject_id"]
    second_id = service.subject_add(
        display_label="Second private label", kind="OPERATOR"
    )["subject_id"]

    with pytest.raises(ServiceError) as exc_info:
        service.context_get(consumer_id="host-agent")

    detail = str(exc_info.value)
    assert "--subject-id" in detail
    assert first_id in detail and second_id in detail
    assert "First private label" not in detail
    assert "Second private label" not in detail


def test_context_get_zero_operator_subjects_errs_with_setup_step(service):
    with pytest.raises(ServiceError) as exc_info:
        service.context_get(consumer_id="host-agent")

    detail = str(exc_info.value)
    assert "setup" in detail
    assert "unknown subject_id" not in detail

    codes = {error.code for error in error_taxonomy(get_operation("context.get"))}
    assert {
        "no_operator_subject",
        "inactive_operator_subject",
        "ambiguous_operator_subject",
    } <= codes


def test_context_get_paused_only_operator_requires_explicit_subject_id(service):
    subject_id = service.setup()["subject_id"]
    service.subject_pause(subject_id=subject_id)

    with pytest.raises(ServiceError) as exc_info:
        service.context_get(consumer_id="host-agent")

    detail = str(exc_info.value)
    assert "subject-list" in detail
    assert "--subject-id" in detail
    assert "paused" in detail
    assert "run 'operator-voice setup'" not in detail
    assert "does not reactivate" in detail
    assert "unknown subject_id" not in detail

    explicit = service.context_get(subject_id=subject_id, consumer_id="host-agent")
    assert explicit["subject_id"] == subject_id


def test_status_next_step_requires_active_operator_subject(service):
    empty = service.status()
    assert "setup" in empty["next_step"]
    assert empty["operator_subject_ids"] == []

    service.subject_add(
        display_label="External only",
        kind="EXTERNAL",
        purposes=["communication_pattern_analysis"],
    )
    external_only = service.status()
    assert "setup" in external_only["next_step"]
    assert external_only["operator_subject_ids"] == []

    subject_id = service.setup()["subject_id"]
    active = service.status()
    assert active["next_step"] == "ready"
    assert active["operator_subject_ids"] == [subject_id]

    service.subject_pause(subject_id=subject_id)
    paused = service.status()
    assert paused["next_step"] != "ready"
    assert "subject-list" in paused["next_step"]
    assert "--subject-id" in paused["next_step"]
    assert "does not reactivate" in paused["next_step"]
    assert paused["operator_subject_ids"] == []


def test_doctor_status_surface_subject_id(service):
    subject_id = service.setup(display_label="Operator label must stay private")["subject_id"]
    inactive_id = service.subject_add(
        display_label="Inactive operator label must stay private", kind="OPERATOR"
    )["subject_id"]
    service.subject_pause(subject_id=inactive_id)
    external_id = service.subject_add(
        display_label="External label must stay private",
        kind="EXTERNAL",
        purposes=["communication_pattern_analysis"],
    )["subject_id"]

    for result in (service.status(), service.doctor()):
        assert result["operator_subject_ids"] == [subject_id]
        assert "--consumer-id" in result["consumer_id_hint"]
        assert "agent/tool" in result["consumer_id_hint"]
        encoded = json.dumps(result)
        assert inactive_id not in encoded
        assert external_id not in encoded
        assert "label must stay private" not in encoded
