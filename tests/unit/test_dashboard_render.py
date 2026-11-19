"""Render-only Cursor dashboard — build plan v4 §13 M4 worklist "render-only Cursor dashboard" +
acceptance "Dashboard/status leak no content; refresh reflects live state".

Proves the generated ``.canvas.tsx`` is STRUCTURALLY render-only (cannot mutate OVT state), leaks
no content, embeds the snapshot, stays byte-stable against the committed artifact, and that a
refresh reflects live state (regenerating from a changed snapshot changes the embedded data).
"""

from __future__ import annotations

import json
from pathlib import Path

from operator_voice.dashboard import generate as dash

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_ARTIFACT = REPO_ROOT / "dashboard" / "operator-voice-status.canvas.tsx"

# OVT operation/handler tokens that MUTATE state. None may appear in a render-only canvas.
_MUTATION_TOKENS = [
    "subject_add", "subject.add", "subject_alias_add", "subject.alias_add", "source_add", "ingest",
    "fact_correct", "feedback_add", "config_set", "gateway_configure", "profile_publish",
    "profile_reconcile", "profile.reconcile", "portability_import", "portability.import",
    "portability_export", "erase", "off(", "rebuild", "reanalyze", "upsert",
]
# Network primitives that would let a canvas reach a live source instead of the embedded snapshot.
_NETWORK_TOKENS = ["fetch(", "XMLHttpRequest", "WebSocket", "navigator.", "http://", "https://", "require("]


def test_render_is_deterministic_for_a_given_snapshot():
    a = dash.render_canvas(dash.SAMPLE_SNAPSHOT)
    b = dash.render_canvas(dash.SAMPLE_SNAPSHOT)
    assert a == b


def test_committed_artifact_is_byte_stable_against_the_generator():
    assert COMMITTED_ARTIFACT.exists(), "committed dashboard artifact is missing"
    expected = dash.render_canvas(dash.SAMPLE_SNAPSHOT)
    assert COMMITTED_ARTIFACT.read_text(encoding="utf-8") == expected, (
        "committed canvas drifted from the generator — regenerate with "
        "`python -m operator_voice.dashboard.generate --sample --out dashboard/operator-voice-status.canvas.tsx`"
    )


def test_canvas_imports_only_from_cursor_canvas():
    tsx = dash.render_canvas(dash.SAMPLE_SNAPSHOT)
    # every ES module import (`from "<module>"`) targets exactly "cursor/canvas"
    froms = [ln for ln in tsx.splitlines() if 'from "' in ln]
    assert froms, "expected at least one import"
    for ln in froms:
        assert 'from "cursor/canvas"' in ln, f"unexpected import source: {ln!r}"


def test_canvas_makes_no_network_call():
    tsx = dash.render_canvas(dash.SAMPLE_SNAPSHOT)
    for tok in _NETWORK_TOKENS:
        assert tok not in tsx, f"render-only canvas must not contain network primitive {tok!r}"


def test_canvas_dispatches_no_ovt_mutation():
    tsx = dash.render_canvas(dash.SAMPLE_SNAPSHOT)
    for tok in _MUTATION_TOKENS:
        assert tok not in tsx, f"render-only canvas must not reference OVT mutation token {tok!r}"


def test_canvas_holds_no_persistent_state_and_only_navigates_on_refresh():
    tsx = dash.render_canvas(dash.SAMPLE_SNAPSHOT)
    # No persistent canvas state (that is the only in-canvas write surface): neither imported nor
    # called. (The header comment may mention the word; we check the import/call forms specifically.)
    assert "useCanvasState," not in tsx     # not in the import list
    assert "useCanvasState(" not in tsx     # never called
    # The single host action is opening a chat to re-run the read-only generator — never an OVT op.
    assert tsx.count("dispatch({") == 1
    assert 'type: "newComposerChat"' in tsx
    for other in ('"openFile"', '"openAgent"'):
        assert other not in tsx


def test_snapshot_is_embedded_and_redacts_external_labels():
    snap = dict(dash.SAMPLE_SNAPSHOT)
    tsx = dash.render_canvas(snap)
    assert "const SNAPSHOT =" in tsx and "as const;" in tsx
    # the embedded JSON round-trips
    body = tsx.split("const SNAPSHOT =", 1)[1].split("as const;", 1)[0].strip()
    parsed = json.loads(body)
    assert parsed["schema"] == "operator-voice-dashboard/v1"
    # external subject labels are redacted (never a real name) in the sample
    ext = [it for it in parsed["subjects"]["items"] if it["kind"] == "EXTERNAL"]
    assert ext and all(it["label"] == "EXTERNAL subject" for it in ext)


def test_refresh_reflects_live_state():
    # "refresh reflects live state": regenerating from a changed snapshot changes the embedded data.
    base = json.loads(json.dumps(dash.SAMPLE_SNAPSHOT))
    changed = json.loads(json.dumps(dash.SAMPLE_SNAPSHOT))
    changed["facts"]["total"] = 999
    changed["reviews"]["open_count"] = 42
    a = dash.render_canvas(base)
    b = dash.render_canvas(changed)
    assert a != b
    assert '"total": 999' in b and '"total": 999' not in a
