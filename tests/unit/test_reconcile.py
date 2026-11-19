"""Deterministic typed reconciliation kernel — build plan v4 §13 M4 worklist 5, D18.

Pure/deterministic coverage of :func:`operator_voice.kernel.reconcile.reconcile_facts`: disjoint
adopt, unchanged, identity-change conflict, conflicting-correction conflict (operator feedback is
P0 and never silently overwritten), deterministic inferred-vs-inferred winner, and input-order
independence.
"""

from __future__ import annotations

from operator_voice.domain.models import FactState
from operator_voice.kernel.reconcile import FactView, reconcile_facts


def _v(facet: str, value, state: FactState, *, scope: str = "general|all|all|en|all", confidence: float = 0.8) -> FactView:
    return FactView(facet=facet, scope_key=scope, value=value, state=state, confidence=confidence)


def test_remote_only_fact_is_adopted_as_disjoint_change():
    local = [_v("tone", "warm", FactState.SUPPORTED)]
    remote = [_v("tone", "warm", FactState.SUPPORTED), _v("sentence_length", "short", FactState.SUPPORTED)]
    result = reconcile_facts(local, remote)
    assert result.conflict_count == 0
    assert [fv.facet for fv in result.adopt] == ["sentence_length"]
    assert result.unchanged == 1  # tone byte-equal + same state


def test_local_only_fact_is_left_alone():
    local = [_v("banned_phrase", "delve", FactState.CORRECTED)]
    remote = []
    result = reconcile_facts(local, remote)
    assert result.adopt == []
    assert result.conflicts == []
    assert result.unchanged == 0  # nothing on the remote side to compare


def test_identity_change_is_a_conflict_never_auto_resolved():
    local = [_v("identity:primary_email", "a@example.com", FactState.SUPPORTED)]
    remote = [_v("identity:primary_email", "b@example.com", FactState.SUPPORTED)]
    result = reconcile_facts(local, remote)
    assert result.merged_count == 0
    assert result.conflict_count == 1
    assert result.conflicts[0].reason == "identity_change"


def test_conflicting_correction_is_a_conflict_in_either_direction():
    # remote correction vs local correction (different values) -> conflict, never overwrite.
    local = [_v("tone", "always warm", FactState.CORRECTED)]
    remote = [_v("tone", "always terse", FactState.CORRECTED)]
    result = reconcile_facts(local, remote)
    assert result.conflict_count == 1
    assert result.conflicts[0].reason == "conflicting_correction"
    assert result.merged_count == 0

    # local inferred, remote CORRECTED, differing -> still a conflict (operator word is P0).
    local2 = [_v("tone", "warm", FactState.SUPPORTED)]
    remote2 = [_v("tone", "terse", FactState.CORRECTED)]
    assert reconcile_facts(local2, remote2).conflicts[0].reason == "conflicting_correction"


def test_two_inferred_facts_pick_a_deterministic_winner():
    # Both SUPPORTED (inferred), differ. Higher confidence wins; if remote wins it is adopted.
    local = [_v("tone", "warm", FactState.SUPPORTED, confidence=0.6)]
    remote = [_v("tone", "terse", FactState.SUPPORTED, confidence=0.9)]
    result = reconcile_facts(local, remote)
    assert result.conflict_count == 0
    assert [fv.value for fv in result.adopt] == ["terse"]  # remote adopted (higher confidence)

    # Reverse confidence: local already wins -> unchanged, nothing adopted.
    local2 = [_v("tone", "warm", FactState.SUPPORTED, confidence=0.9)]
    remote2 = [_v("tone", "terse", FactState.SUPPORTED, confidence=0.6)]
    result2 = reconcile_facts(local2, remote2)
    assert result2.adopt == []
    assert result2.unchanged == 1


def test_decision_is_independent_of_input_ordering():
    local = [
        _v("a", "1", FactState.SUPPORTED, confidence=0.5),
        _v("b", "x", FactState.CORRECTED),
        _v("identity:x", "p", FactState.SUPPORTED),
    ]
    remote = [
        _v("a", "2", FactState.SUPPORTED, confidence=0.9),   # remote wins -> adopt
        _v("b", "y", FactState.CORRECTED),                   # conflicting correction
        _v("identity:x", "q", FactState.SUPPORTED),          # identity conflict
        _v("c", "new", FactState.SUPPORTED),                 # disjoint -> adopt
    ]
    r1 = reconcile_facts(local, remote)
    r2 = reconcile_facts(list(reversed(local)), list(reversed(remote)))
    assert sorted(fv.facet for fv in r1.adopt) == sorted(fv.facet for fv in r2.adopt) == ["a", "c"]
    assert {c.reason for c in r1.conflicts} == {c.reason for c in r2.conflicts} == {"identity_change", "conflicting_correction"}


def test_byte_equal_same_state_is_unchanged_not_adopted():
    local = [_v("tone", {"nested": [1, 2]}, FactState.SUPPORTED)]
    remote = [_v("tone", {"nested": [1, 2]}, FactState.SUPPORTED)]
    result = reconcile_facts(local, remote)
    assert result.unchanged == 1
    assert result.adopt == [] and result.conflicts == []
