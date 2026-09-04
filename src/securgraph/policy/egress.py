"""Tenant egress policy: parsing, decision, and enforcement.

The policy is a runtime setting (`egress_policy`) shaped as::

    {"default": "allow" | "block",
     "rules": [{"provider": ..., "stage": ..., "action": ...}]}

Rules match on provider and stage only. A `max_classification` dimension was
accepted once but never enforced: the single call site could not know the
classification of the evidence about to leave, so enforcement ran against a
hardcoded "internal". A rule scoped to `internal` therefore also released
`restricted` evidence, and a rule scoped to `public` never matched at all,
while the UI offered exactly that shape as its default example. Rather than
keep accepting a field that is dropped on the floor, the payload is now
refused. Enforcing it properly means deriving a classification ceiling from
the policy and pushing it into `QueryScope.max_classification` — which both
retrieval lanes already honor — plus a pre-call backstop for the community
lane, which has no scope filter. See docs/technical/06-security-model.md.

Framework-free module so both the settings routes (validation + advisory
cost estimate) and the LLM adapter dispatch (real enforcement) share one
implementation. Validation errors raise ``ValueError`` — API routes wrap
them into HTTP 400.

Enforcement semantics: a tenant with NO configured policy is allowed
(pre-existing behaviour); once a policy is stored, `enforce_egress` raises
``EgressBlockedError`` before any blocked provider call is made.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

_LOG = structlog.get_logger(__name__)

EGRESS_ACTIONS = frozenset({"allow", "block"})
EGRESS_STAGES = frozenset({"extract", "judge", "generation", "embedding", "any"})
EGRESS_PROVIDERS = frozenset({"openai", "anthropic", "gemini", "mistral", "ollama", "any"})

# LLMRole -> egress stage name used in policy rules.
ROLE_TO_STAGE = {
    "extract": "extract",
    "judge": "judge",
    "generation": "generation",
}


@dataclass(frozen=True)
class EgressDecision:
    allowed: bool
    action: str
    reason: str
    matched_rule_index: int | None = None


class EgressBlockedError(RuntimeError):
    """Raised when a tenant egress policy blocks an outbound provider call."""

    def __init__(self, tenant_id: str, provider: str, stage: str, reason: str) -> None:
        self.tenant_id = tenant_id
        self.provider = provider
        self.stage = stage
        self.reason = reason
        super().__init__(
            f"Egress blocked by tenant policy: provider={provider} stage={stage} ({reason})"
        )


def parse_egress_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize and validate an egress policy payload.

    Accepts either the stored ``{"value": {...}}`` wrapper or the bare policy
    object. Raises ``ValueError`` on any invalid field.
    """
    payload = value or {}
    raw_value = payload.get("value", payload)
    if not isinstance(raw_value, dict):
        raise ValueError("egress_policy value must be an object")
    default_action = raw_value.get("default", "block")
    if default_action not in EGRESS_ACTIONS:
        raise ValueError("egress_policy.default must be allow or block")
    rules = raw_value.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("egress_policy.rules must be a list")
    normalized_rules: list[dict[str, str]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"egress_policy.rules[{index}] must be an object")
        provider = str(rule.get("provider", "any"))
        stage = str(rule.get("stage", "any"))
        action = str(rule.get("action", "block"))
        if "max_classification" in rule:
            raise ValueError(
                f"egress_policy.rules[{index}] sets max_classification, which is not "
                "enforced: rules match on provider and stage only. Remove the field "
                "rather than rely on a filter that does not run."
            )
        if provider not in EGRESS_PROVIDERS:
            raise ValueError(f"Unknown egress provider: {provider}")
        if stage not in EGRESS_STAGES:
            raise ValueError(f"Unknown egress stage: {stage}")
        if action not in EGRESS_ACTIONS:
            raise ValueError(f"Unknown egress action: {action}")
        normalized_rules.append(
            {
                "provider": provider,
                "stage": stage,
                "action": action,
            }
        )
    return {"default": default_action, "rules": normalized_rules}


def decide_egress(
    policy: dict[str, Any] | None,
    *,
    provider: str,
    stage: str,
) -> EgressDecision:
    """Evaluate a (already stored) policy for one outbound call.

    A ``None``/absent policy evaluates against the conservative
    ``default=block`` baseline — callers that want "no policy means allow"
    (enforcement) must check for policy presence first.
    """
    normalized = parse_egress_policy(policy or {"value": {"default": "block", "rules": []}})
    for index, rule in enumerate(normalized["rules"]):
        if rule["provider"] not in {"any", provider}:
            continue
        if rule["stage"] not in {"any", stage}:
            continue
        return EgressDecision(
            allowed=rule["action"] == "allow",
            action=rule["action"],
            reason="matched explicit egress rule",
            matched_rule_index=index,
        )
    default_action = normalized["default"]
    return EgressDecision(
        allowed=default_action == "allow",
        action=default_action,
        reason="default egress policy",
    )


def enforce_egress(
    tenant_id: str | None,
    provider: str,
    stage: str,
) -> None:
    """Block an outbound LLM call when the tenant's egress policy says so.

    No tenant or no configured policy -> allowed (pre-policy behaviour).
    A stored-but-invalid policy fails CLOSED (blocked): a tenant that opted
    into egress control must not silently fall open on a bad payload.
    """
    if tenant_id is None:
        return
    from securgraph.runtime_overrides import get_runtime_overrides

    policy = get_runtime_overrides(tenant_id).get("egress_policy")
    if policy is None:
        return
    try:
        decision = decide_egress(
            policy if isinstance(policy, dict) else None,
            provider=provider,
            stage=stage,
        )
    except ValueError as exc:
        # The parse detail describes the stored policy document, not this
        # caller's request, and the registry exposes an EgressBlockedError
        # message verbatim. It goes to the log; the tenant gets a fixed reason.
        _LOG.error(
            "egress_policy_unparseable",
            tenant_id=tenant_id,
            provider=provider,
            stage=stage,
            error=str(exc),
        )
        raise EgressBlockedError(
            tenant_id, provider, stage, "the stored egress policy is not valid"
        ) from exc
    if not decision.allowed:
        raise EgressBlockedError(tenant_id, provider, stage, decision.reason)
