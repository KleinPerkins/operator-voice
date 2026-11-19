"""Integration coverage for the CLI, MCP server, and intent-alias interfaces — build plan v3 §6.
Every fixture here uses :class:`InMemoryKeyStore` (never the real Keychain) and an isolated
temp-dir XDG runtime, monkeypatching each interface module's ``open_runtime`` reference so no test
touches the operator's real Keychain, database, or config."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from operator_voice.persistence.crypto import InMemoryKeyStore
from operator_voice.runtime.bootstrap import open_runtime
from operator_voice.runtime.paths import RuntimePaths


@pytest.fixture
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = RuntimePaths(
        config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state"
    )

    keystore = InMemoryKeyStore()

    def _open(paths_arg=None, keystore_factory=None):
        return open_runtime(paths=paths, keystore_factory=lambda: keystore)

    monkeypatch.setattr("operator_voice.interfaces.cli.open_runtime", _open)
    monkeypatch.setattr("operator_voice.interfaces.mcp.open_runtime", _open)
    return paths


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def test_cli_status_brief_reports_version(isolated_runtime, capsys):
    import operator_voice
    from operator_voice.interfaces import cli
    assert cli.run(["status", "--brief"]) == 0
    output = capsys.readouterr().out
    assert output.startswith(f"operator-voice {operator_voice.__version__}: ")
    assert len(output.splitlines()) == 1


def test_cli_and_mcp_context_get_emit_compact_bounded_payloads(isolated_runtime, capsys):
    from operator_voice.interfaces import cli, mcp
    from operator_voice.kernel.retrieval import estimate_tokens
    assert cli.run(["setup"]) == 0
    capsys.readouterr()
    assert cli.run(["context-get", "--consumer-id", "test-consumer"]) == 0
    cli_text = capsys.readouterr().out.strip()
    mcp_text = asyncio.run(mcp.dispatch_tool_call("context_get", {"consumer_id": "test-consumer"}))[0].text
    for text in (cli_text, mcp_text):
        payload = json.loads(text)
        assert "\n" not in text
        assert estimate_tokens(text) <= payload["actual_tokens"] <= payload["budget_tokens"]


def test_cli_status_returns_json(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    rc = cli.run(["status"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "status" in out or isinstance(out, dict)


def test_cli_setup_then_subject_add_then_list(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    assert cli.run(["setup", "--display-label", "operator"]) == 0
    capsys.readouterr()

    assert cli.run(["subject-add", "--display-label", "Alex"]) == 0
    added = json.loads(capsys.readouterr().out)
    assert added.get("subject_id") or added.get("status")

    assert cli.run(["subject-list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert isinstance(listed, (dict, list))


def test_cli_slack_status_brief_prints_plain_text_stale_line(isolated_runtime, capsys):
    """Success criteria: 'operator-voice slack-status --brief prints an actionable stale/fresh
    line' — a fresh install (never imported) must print plain text, not JSON."""
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    rc = cli.run(["slack-status", "--brief"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "slack-voice: stale — ask agent to refresh Slack voice; 0 of 0 messages learned from"
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)  # confirms this is really plain text, not a JSON blob


def test_cli_slack_status_without_brief_prints_full_json(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    assert cli.run(["slack-status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stale"] is True
    assert "brief" in out


def test_cli_slack_import_then_status_reports_fresh(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    messages = json.dumps([{
        "channel": "#general", "message_ts": "1700000000.000100",
        "text": "A CLI-imported operator message, long enough to keep.", "author_id": "U1",
    }])
    assert cli.run(["slack-import", "--author-id", "U1", "--messages", messages]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["imported"] == 1

    assert cli.run(["slack-status", "--brief"]) == 0
    brief = capsys.readouterr().out.strip()
    assert brief.startswith("slack-voice: fresh")


def test_cli_slack_import_falls_back_to_stdin_when_no_flags_given(isolated_runtime, capsys, monkeypatch):
    """§ OVT Slack Adapter 1b: CLI-only stdin fallback when neither ``--file`` nor ``--messages``
    is given — the same ``{author_id, messages}`` envelope shape ``--file`` accepts."""
    import io

    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    envelope = {"author_id": "U1", "messages": [
        {"channel": "#general", "message_ts": "1700000000.000100", "text": "A stdin-imported operator message, long enough to keep.", "author_id": "U1"},
    ]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(envelope)))

    rc = cli.run(["slack-import"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["imported"] == 1


def test_cli_drive_status_brief_prints_plain_text_stale_line(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    rc = cli.run(["drive-status", "--brief"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "drive-voice: stale — ask agent to refresh Drive voice; no registered folders — select folders containing your own writing"
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_cli_drive_status_without_brief_prints_full_json(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    assert cli.run(["drive-status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stale"] is True
    assert "brief" in out


def test_cli_drive_import_then_status_reports_fresh(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    assert cli.run(["drive-folder_add", "--folder-id", "writing"]) == 0
    capsys.readouterr()
    files = json.dumps([{
        "file_id": "file-1", "revision_id": "rev-1", "drive_id": None,
        "owners": ["op@example.com"], "owner_id": "op@example.com",
        "name": "Memo", "folder_id": "writing", "mime_type": "application/vnd.google-apps.document",
        "modified_time": "2026-07-30T14:02:00Z",
        "text": "A CLI-imported operator document, long enough to keep.",
    }])
    assert cli.run(["drive-import", "--owner-id", "op@example.com", "--files", files]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["imported"] == 1

    assert cli.run(["drive-status", "--brief"]) == 0
    brief = capsys.readouterr().out.strip()
    assert brief.startswith("drive-voice: fresh")


def test_cli_drive_import_falls_back_to_stdin_when_no_flags_given(isolated_runtime, capsys, monkeypatch):
    import io

    from operator_voice.interfaces import cli

    cli.run(["setup"])
    assert cli.run(["drive-folder_add", "--folder-id", "writing"]) == 0
    capsys.readouterr()
    envelope = {
        "owner_id": "op@example.com",
        "files": [{
            "file_id": "file-1", "revision_id": "rev-1", "drive_id": None,
            "owners": ["op@example.com"], "owner_id": "op@example.com",
            "name": "Memo", "folder_id": "writing", "mime_type": "application/vnd.google-apps.document",
            "modified_time": "2026-07-30T14:02:00Z",
            "text": "A stdin-imported operator document, long enough to keep.",
        }],
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(envelope)))

    rc = cli.run(["drive-import"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["imported"] == 1


def test_cli_gmail_status_brief_prints_plain_text_stale_line(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    rc = cli.run(["gmail-status", "--brief"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "gmail-voice: stale — ask agent to refresh Gmail voice"
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_cli_gmail_import_then_status_reports_fresh(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    messages = json.dumps([{
        "message_id": "msg-1", "thread_id": "thread-1", "internal_date": "2026-07-30T14:02:00Z",
        "from": "op@example.com", "subject": "Hello",
        "text": "A CLI-imported operator Sent message, long enough to keep.",
    }])
    assert cli.run(["gmail-import", "--author-email", "op@example.com", "--messages", messages]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["imported"] == 1

    assert cli.run(["gmail-status", "--brief"]) == 0
    brief = capsys.readouterr().out.strip()
    assert brief.startswith("gmail-voice: fresh")


def test_cli_gmail_import_falls_back_to_stdin_when_no_flags_given(isolated_runtime, capsys, monkeypatch):
    import io

    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    envelope = {
        "author_email": "op@example.com",
        "messages": [{
            "message_id": "msg-1", "thread_id": "thread-1", "internal_date": "2026-07-30T14:02:00Z",
            "from": "op@example.com", "subject": "Hello",
            "text": "A stdin-imported operator Sent message, long enough to keep.",
        }],
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(envelope)))

    rc = cli.run(["gmail-import"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["imported"] == 1


def test_cli_google_status_brief_prints_plain_text_stale_line(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    rc = cli.run(["google-status", "--brief"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("google-voice: stale")
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_cli_google_status_without_brief_prints_full_json(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    assert cli.run(["google-status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stale"] is True
    assert "drive" in out and "gmail" in out


def test_cli_erase_requires_confirmation(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    cli.run(["setup"])
    capsys.readouterr()
    rc = cli.run(["erase", "--scope", "full_system"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "confirmation_required"


def test_cli_unknown_operation_exits_nonzero(isolated_runtime):
    from operator_voice.interfaces import cli

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["not-a-real-operation"])


def test_cli_every_operation_has_a_subcommand():
    from operator_voice.domain.operations import OPERATIONS
    from operator_voice.interfaces import cli

    parser = cli.build_parser()
    choices = parser._subparsers._group_actions[0].choices
    for op in OPERATIONS:
        assert op.cli_name in choices, f"{op.name} missing from generated CLI"


def test_every_operation_has_a_callable_service_handler():
    """§6 single-source invariant: the registry generates every surface, so each declared operation
    MUST resolve to a callable ``OperatorVoiceService`` handler with exactly its declared kw-args.
    Guards against a registry entry whose handler was never implemented (a silent 500 at dispatch)."""
    import inspect

    from operator_voice.application.service import OperatorVoiceService
    from operator_voice.domain.operations import OPERATIONS

    for op in OPERATIONS:
        handler = getattr(OperatorVoiceService, op.handler_name, None)
        assert callable(handler), f"{op.name} has no service handler {op.handler_name!r}"
        params = set(inspect.signature(handler).parameters) - {"self"}
        declared = {a.name for a in op.args}
        assert declared <= params, f"{op.name} declares args {declared - params} the handler does not accept"


# ---------------------------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------------------------


def test_mcp_read_only_default_hides_mutations(isolated_runtime, monkeypatch):
    monkeypatch.delenv("OPERATOR_VOICE_MCP_ENABLE_MUTATIONS", raising=False)
    from operator_voice.interfaces import mcp as ovt_mcp

    tools = asyncio.run(ovt_mcp.list_available_tools())
    names = {t.name for t in tools}
    assert "status" in names
    assert "erase" not in names
    assert "subject_add" not in names


def test_mcp_mutations_enabled_via_env(isolated_runtime, monkeypatch):
    monkeypatch.setenv("OPERATOR_VOICE_MCP_ENABLE_MUTATIONS", "1")
    from operator_voice.interfaces import mcp as ovt_mcp

    tools = asyncio.run(ovt_mcp.list_available_tools())
    names = {t.name for t in tools}
    assert "subject_add" in names
    assert "setup" not in names  # lifecycle stays CLI-only regardless of the mutation flag


def test_mcp_build_server_smoke(isolated_runtime):
    from operator_voice.interfaces import mcp as ovt_mcp

    server = ovt_mcp.build_server()
    assert server is not None


def test_mcp_call_tool_status(isolated_runtime, monkeypatch):
    monkeypatch.delenv("OPERATOR_VOICE_MCP_ENABLE_MUTATIONS", raising=False)
    from operator_voice.interfaces import mcp as ovt_mcp

    result = asyncio.run(ovt_mcp.dispatch_tool_call("status", {}))
    payload = json.loads(result[0].text)
    assert isinstance(payload, dict)


def test_mcp_call_tool_unknown_name_is_an_error_not_a_crash(isolated_runtime, monkeypatch):
    monkeypatch.delenv("OPERATOR_VOICE_MCP_ENABLE_MUTATIONS", raising=False)
    from operator_voice.interfaces import mcp as ovt_mcp

    result = asyncio.run(ovt_mcp.dispatch_tool_call("erase", {"scope": "full_system"}))
    payload = json.loads(result[0].text)
    assert payload["status"] == "error"


def test_mcp_exposes_full_active_resource_set(isolated_runtime):
    from operator_voice.docs.resources import ACTIVE_RESOURCE_URIS
    from operator_voice.interfaces import mcp as ovt_mcp

    resources = asyncio.run(ovt_mcp.list_available_resources())
    uris = {str(r.uri) for r in resources}
    # M2 activates the whole registry, including the previously-inert m2 guidance + learning status
    assert "ovt://guidance/template-integration" in uris
    assert "ovt://status/learning" in uris
    assert uris == {str(u) for u in ACTIVE_RESOURCE_URIS}


def test_mcp_reads_a_static_m2_resource_without_touching_runtime(isolated_runtime):
    from operator_voice.interfaces import mcp as ovt_mcp

    body = asyncio.run(ovt_mcp.dispatch_read_resource("ovt://guidance/template-integration"))
    assert "never imports OVT internals" in body
    assert "/Users/" not in body


def test_mcp_lists_and_renders_prompts(isolated_runtime):
    from operator_voice.interfaces import mcp as ovt_mcp

    prompts = asyncio.run(ovt_mcp.list_available_prompts())
    names = {p.name for p in prompts}
    assert {"voice-matched-draft", "correct-my-voice", "external-subject-brief"} <= names

    result = asyncio.run(ovt_mcp.dispatch_get_prompt(
        "voice-matched-draft",
        {"subject_id": "subj_x", "consumer_id": "demo", "task": "write a note"},
    ))
    text = result.messages[0].content.text
    assert "subj_x" in text and "draft.validate" in text


def test_cli_context_get_rejects_incompatible_consumer_version(isolated_runtime, capsys):
    from operator_voice.interfaces import cli

    assert cli.run(["setup", "--display-label", "operator"]) == 0
    setup_out = json.loads(capsys.readouterr().out)
    subject_id = setup_out["subject_id"]

    # a v1-compatible consumer succeeds
    assert cli.run(["context-get", "--subject-id", subject_id, "--consumer-id", "demo",
                    "--accepted-major-versions", '["v1"]']) == 0
    ok = json.loads(capsys.readouterr().out)
    assert ok["schema"] == "voice-context-bundle/v1"

    # a v2-only consumer is refused (negotiation fails closed; CLI signals error rc)
    assert cli.run(["context-get", "--subject-id", subject_id, "--consumer-id", "demo",
                    "--accepted-major-versions", '["v2"]']) == 1
    refused = json.loads(capsys.readouterr().out)
    assert refused["status"] == "error"
    assert "no compatible bundle version" in refused["detail"]


# ---------------------------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------------------------


def test_intents_resolve_matches_known_operations():
    from operator_voice.domain.operations import OPERATIONS_BY_NAME
    from operator_voice.interfaces.intents import ALIASES, resolve

    for op_name, phrases in ALIASES.items():
        assert op_name in OPERATIONS_BY_NAME
        for phrase in phrases:
            assert resolve(phrase) == op_name


def test_intents_resolve_slack_import_and_status_aliases():
    """OVT Slack Adapter v1 (plan §2b): natural-language routing for the new operations.
    ``test_intents_resolve_matches_known_operations`` above already exercises every phrase in
    ``ALIASES`` generically (including these); this test additionally pins the specific phrasings
    the plan calls out by name, so a future edit that silently drops or renames one fails loudly
    here rather than only inside a generic loop."""
    from operator_voice.interfaces.intents import resolve

    for phrase in ("harvest slack", "pick up my slack", "refresh slack voice", "sync slack messages", "import my slack"):
        assert resolve(phrase) == "slack.import", phrase
    for phrase in ("slack voice status", "is slack set up", "when did you last read slack"):
        assert resolve(phrase) == "slack.status", phrase


def test_intents_resolve_drive_gmail_google_import_and_status_aliases():
    """Google channel adapters: natural-language routing pins for Drive/Gmail/google.status."""
    from operator_voice.interfaces.intents import resolve

    for phrase in (
        "harvest drive", "pick up my drive", "refresh drive voice", "sync my google docs",
        "import my drive", "pull my docs into voice",
    ):
        assert resolve(phrase) == "drive.import", phrase
    for phrase in ("drive voice status", "is drive set up", "when did you last read drive"):
        assert resolve(phrase) == "drive.status", phrase
    for phrase in (
        "harvest gmail", "pick up my sent mail", "refresh gmail voice", "sync my sent email",
        "import my gmail",
    ):
        assert resolve(phrase) == "gmail.import", phrase
    for phrase in (
        "gmail voice status", "is gmail set up", "when did you last read gmail",
        "check if my sent mail is synced",
    ):
        assert resolve(phrase) == "gmail.status", phrase
    for phrase in (
        "google voice status", "refresh my google voice", "is my google stuff synced",
        "drive and gmail status",
    ):
        assert resolve(phrase) == "google.status", phrase


def test_intents_resolve_context_get_slack_draft_aliases():
    from operator_voice.interfaces.intents import resolve

    assert resolve("draft this for slack") == "context.get"
    assert resolve("slack message in my voice") == "context.get"


def test_intents_resolve_returns_none_for_gibberish():
    from operator_voice.interfaces.intents import resolve

    assert resolve("asdkjhasdkjh qweoiqwe") is None


def test_intents_resolve_empty_string():
    from operator_voice.interfaces.intents import resolve

    assert resolve("") is None
