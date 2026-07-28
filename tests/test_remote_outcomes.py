from app.core.remote_outcomes import policy_for_remote_outcome


def test_uncertain_remote_outcomes_have_one_blocking_p1_policy() -> None:
    for outcome, write_occurred in (
        ("partially_applied", True),
        ("unknown_remote_state", None),
        ("identity_conflict", True),
    ):
        policy = policy_for_remote_outcome(outcome)
        assert policy.task_status == "blocked"
        assert policy.severity == "P1"
        assert policy.remote_write_occurred is write_occurred
        assert policy.auto_retry_allowed is False
        assert policy.create_observation is False
    assert (
        policy_for_remote_outcome("identity_conflict").error_code
        == "SHOPIFY_ARTICLE_IDENTITY_CONFLICT"
    )
