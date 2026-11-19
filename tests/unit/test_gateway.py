"""The model gateway — build plan §D7 (disabled by default, injected, policy-gated) + §13 M2
worklist 4 (provider adapters, provider/model/data-class allowlist, separate default-deny EXTERNAL
egress, zero payload retention, cancellation, timeout degradation). Synthetic signals only; the
bundled provider is a no-network local deterministic transform."""

from __future__ import annotations

import time

import pytest

from operator_voice.application.gateway import (
    DISABLED_GATEWAY,
    CallableProvider,
    GatewayCallRequest,
    GatewayDisabledError,
    GatewayOutcome,
    LocalDeterministicProvider,
    ModelGateway,
    default_gateway,
    gateway_from_config,
)
from operator_voice.domain.models import GatewayConfig


def _enabled_config(**kw) -> GatewayConfig:
    base = dict(
        operator_egress_enabled=True, external_egress_enabled=False,
        provider="local-deterministic", model="review-v0", allowed_data_classes=["operator"],
        timeout_ms=20000,
    )
    base.update(kw)
    return GatewayConfig(**base)


# ---- default-off posture (unchanged from M1) --------------------------------------------------


def test_default_gateway_is_disabled():
    gw = default_gateway()
    assert gw.enabled is False
    assert gw is DISABLED_GATEWAY


def test_new_gateway_defaults_to_disabled():
    assert ModelGateway().enabled is False


def test_disabled_gateway_call_raises_gateway_disabled_error():
    gw = ModelGateway(enabled=False)
    request = GatewayCallRequest(purpose="test", data_class="operator", provider="none", model="none", signal="x")
    with pytest.raises(GatewayDisabledError):
        gw.call(request)


def test_gateway_status_reports_disabled_and_no_provider():
    status = default_gateway().status()
    assert status["enabled"] is False
    assert status["provider"] is None
    assert status["external_egress_enabled"] is False


# ---- M2 policy path ---------------------------------------------------------------------------


def test_enabled_gateway_with_matching_policy_succeeds_and_returns_text():
    gw = gateway_from_config(_enabled_config())
    req = GatewayCallRequest(purpose="semantic_review", data_class="operator",
                             provider="local-deterministic", model="review-v0", signal="a synthetic draft")
    result, decision = gw.attempt(req)
    assert result.outcome == GatewayOutcome.SUCCESS.value
    assert result.text is not None  # in-memory result only
    assert decision.outcome is GatewayOutcome.SUCCESS


def test_unapproved_provider_is_denied_closed_not_raised():
    gw = gateway_from_config(_enabled_config())
    req = GatewayCallRequest(purpose="x", data_class="operator", provider="some-other", model="review-v0", signal="s")
    result, _ = gw.attempt(req)
    assert result.outcome == GatewayOutcome.DENIED_PROVIDER.value
    assert result.text is None


def test_unapproved_model_is_denied():
    gw = gateway_from_config(_enabled_config())
    req = GatewayCallRequest(purpose="x", data_class="operator", provider="local-deterministic", model="ghost", signal="s")
    result, _ = gw.attempt(req)
    assert result.outcome == GatewayOutcome.DENIED_MODEL.value


def test_unallowlisted_data_class_is_denied():
    gw = gateway_from_config(_enabled_config(allowed_data_classes=["operator"]))
    req = GatewayCallRequest(purpose="x", data_class="stylometric", provider="local-deterministic", model="review-v0", signal="s")
    result, _ = gw.attempt(req)
    assert result.outcome == GatewayOutcome.DENIED_DATA_CLASS.value


def test_raw_and_quarantined_text_is_never_eligible_even_if_allowlisted():
    # Even if an operator somehow put "raw" in the allowlist, the never-eligible gate fires first.
    gw = gateway_from_config(_enabled_config(allowed_data_classes=["operator", "raw", "quarantined"]))
    for cls in ("raw", "quarantined"):
        req = GatewayCallRequest(purpose="x", data_class=cls, provider="local-deterministic", model="review-v0", signal="s")
        result, _ = gw.attempt(req)
        assert result.outcome == GatewayOutcome.DENIED_INELIGIBLE.value, cls


def test_ineligible_data_class_check_is_case_and_whitespace_insensitive():
    """Security-review hardening: a mixed-case/padded ineligible label can never dodge the
    never-eligible gate, even if that exact spelling were allowlisted."""
    gw = gateway_from_config(_enabled_config(allowed_data_classes=["operator", " Raw ", "QUARANTINED"]))
    for cls in (" Raw ", "QUARANTINED", "rAw"):
        req = GatewayCallRequest(purpose="x", data_class=cls, provider="local-deterministic", model="review-v0", signal="s")
        result, _ = gw.attempt(req)
        assert result.outcome == GatewayOutcome.DENIED_INELIGIBLE.value, cls


def test_external_label_variant_still_hits_external_switch():
    # "EXTERNAL"/" External " normalize to the external data class and stay behind the separate switch.
    gw = gateway_from_config(_enabled_config(allowed_data_classes=["operator", "external"]))
    req = GatewayCallRequest(purpose="x", data_class=" External ", provider="local-deterministic", model="review-v0", signal="s")
    result, _ = gw.attempt(req)
    assert result.outcome == GatewayOutcome.DENIED_EXTERNAL_EGRESS.value


def test_external_egress_is_a_separate_switch_denied_even_when_operator_egress_on():
    # operator egress ON, external egress OFF: an EXTERNAL-derived signal is denied.
    gw = gateway_from_config(_enabled_config(allowed_data_classes=["operator", "external"]))
    req = GatewayCallRequest(purpose="x", data_class="operator", provider="local-deterministic",
                             model="review-v0", signal="s", is_external=True)
    result, _ = gw.attempt(req)
    assert result.outcome == GatewayOutcome.DENIED_EXTERNAL_EGRESS.value

    # flipping ONLY the external switch on allows it
    gw2 = gateway_from_config(_enabled_config(allowed_data_classes=["operator", "external"], external_egress_enabled=True))
    result2, _ = gw2.attempt(req)
    assert result2.outcome == GatewayOutcome.SUCCESS.value


def test_external_data_class_denied_when_external_switch_off():
    gw = gateway_from_config(_enabled_config(allowed_data_classes=["operator", "external"]))
    req = GatewayCallRequest(purpose="x", data_class="external", provider="local-deterministic", model="review-v0", signal="s")
    result, _ = gw.attempt(req)
    assert result.outcome == GatewayOutcome.DENIED_EXTERNAL_EGRESS.value


def test_live_off_switch_cancels_egress_after_policy_passes():
    gw = gateway_from_config(_enabled_config(), is_off=lambda: True)
    req = GatewayCallRequest(purpose="x", data_class="operator", provider="local-deterministic", model="review-v0", signal="s")
    result, _ = gw.attempt(req)
    assert result.outcome == GatewayOutcome.CANCELLED.value
    assert result.metadata["cancelled"] is True


def test_slow_provider_times_out_and_degrades():
    slow = {"slowprov": CallableProvider("slowprov", ("m",), lambda model, signal, purpose: (time.sleep(0.5) or "late"))}
    gw = gateway_from_config(
        _enabled_config(provider="slowprov", model="m", timeout_ms=30), providers=slow
    )
    req = GatewayCallRequest(purpose="x", data_class="operator", provider="slowprov", model="m", signal="s")
    result, _ = gw.attempt(req)
    assert result.outcome == GatewayOutcome.TIMEOUT.value
    assert result.text is None


def test_provider_error_degrades_not_crashes():
    def _boom(model, signal, purpose):
        raise RuntimeError("provider blew up")

    bad = {"badprov": CallableProvider("badprov", ("m",), _boom)}
    gw = gateway_from_config(_enabled_config(provider="badprov", model="m"), providers=bad)
    req = GatewayCallRequest(purpose="x", data_class="operator", provider="badprov", model="m", signal="s")
    result, _ = gw.attempt(req)
    assert result.outcome == GatewayOutcome.PROVIDER_ERROR.value


def test_local_provider_is_no_network_and_deterministic():
    p = LocalDeterministicProvider()
    a = p.complete(model="review-v0", signal="hello", purpose="x")
    b = p.complete(model="review-v0", signal="hello", purpose="x")
    assert a == b  # deterministic


def test_no_application_code_hardcodes_an_enabled_gateway():
    """Structural guard: enabling the gateway must come from persisted operator config, never a
    hardcoded ``ModelGateway(enabled=True)`` in shipped source. gateway_from_config passes
    ``enabled=config.operator_egress_enabled`` (a variable), so this stays clean."""
    import pathlib
    import re

    src_root = pathlib.Path(__file__).resolve().parents[2] / "src" / "operator_voice"
    pattern = re.compile(r"ModelGateway\(\s*enabled\s*=\s*True")
    offenders = [p for p in src_root.rglob("*.py") if pattern.search(p.read_text())]
    assert offenders == [], f"hardcoded enabled gateway construction found: {offenders}"
