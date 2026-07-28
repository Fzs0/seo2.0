"""Single policy map for remote write outcomes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteOutcomePolicy:
    task_status: str
    severity: str
    remote_write_occurred: bool | None
    auto_retry_allowed: bool
    requires_fresh_token: bool
    create_observation: bool
    error_code: str


_POLICIES = {
    "confirmed_applied": RemoteOutcomePolicy(
        "done", "P2", True, False, False, True, "REMOTE_CONFIRMED_APPLIED"
    ),
    "confirmed_absent": RemoteOutcomePolicy(
        "blocked", "P1", False, False, True, False, "REMOTE_CONFIRMED_ABSENT"
    ),
    "partially_applied": RemoteOutcomePolicy(
        "blocked", "P1", True, False, True, False, "REMOTE_PARTIALLY_APPLIED"
    ),
    "unknown_remote_state": RemoteOutcomePolicy(
        "blocked", "P1", None, False, True, False, "REMOTE_UNKNOWN_STATE"
    ),
    "identity_conflict": RemoteOutcomePolicy(
        "blocked",
        "P1",
        True,
        False,
        True,
        False,
        "SHOPIFY_ARTICLE_IDENTITY_CONFLICT",
    ),
}


def policy_for_remote_outcome(outcome: str | None) -> RemoteOutcomePolicy | None:
    if not outcome:
        return None
    try:
        return _POLICIES[outcome]
    except KeyError as error:
        raise ValueError(f"unsupported remote outcome: {outcome}") from error


def blocks_automatic_retry(outcome: str | None) -> bool:
    policy = policy_for_remote_outcome(outcome)
    return bool(policy and not policy.auto_retry_allowed)


__all__ = ["RemoteOutcomePolicy", "blocks_automatic_retry", "policy_for_remote_outcome"]
