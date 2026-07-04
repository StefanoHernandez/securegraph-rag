"""Tenant egress policy: parsing, decision, and enforcement.

The policy is a runtime setting (`egress_policy`) shaped as::

    {"default": "allow" | "block",
     "rules": [{"provider": ..., "stage": ..., "max_classification": ..., "action": ...}]}

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

CLASSIFICATION_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
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
        classification = str(rule.get("max_classification", "internal"))
        action = str(rule.get("action", "block"))
        if provider not in EGRESS_PROVIDERS:
            raise ValueError(f"Unknown egress provider: {provider}")
        if stage not in EGRESS_STAGES:
            raise ValueError(f"Unknown egress stage: {stage}")
        if classification not in CLASSIFICATION_RANK:
            raise ValueError(f"Unknown egress classification: {classification}")
        if action not in EGRESS_ACTIONS:
            raise ValueError(f"Unknown egress action: {action}")
        normalized_rules.append(
            {
                "provider": provider,
                "stage": stage,
                "max_classification": classification,
                "action": action,
            }
        )
    return {"default": default_action, "rules": normalized_rules}


def decide_egress(
    policy: dict[str, Any] | None,
    *,
    provider: str,
    stage: str,
    classification: str,
) -> EgressDecision:
    """Evaluate a (already stored) policy for one outbound call.

    A ``None``/absent policy evaluates against the conservative
    ``default=block`` baseline — callers that want "no policy means allow"
    (enforcement) must check for policy presence first.
    """
    normalized = parse_egress_policy(policy or {"value": {"default": "block", "rules": []}})
    request_rank = CLASSIFICATION_RANK[classification]
    for index, rule in enumerate(normalized["rules"]):
        if rule["provider"] not in {"any", provider}:
            continue
        if rule["stage"] not in {"any", stage}:
            continue
        if request_rank > CLASSIFICATION_RANK[rule["max_classification"]]:
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
    classification: str = "internal",
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
            classification=classification,
        )
    except ValueError as exc:
        raise EgressBlockedError(
            tenant_id, provider, stage, f"invalid stored egress policy: {exc}"
        ) from exc
    if not decision.allowed:
        raise EgressBlockedError(tenant_id, provider, stage, decision.reason)
