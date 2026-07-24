from __future__ import annotations

import pytest

from app.services import social_delivery_service


@pytest.mark.asyncio
async def test_confirm_social_jobs_deduplicates_and_keeps_partial_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_confirm(_session: object, job_id: str, *, business_id: str) -> dict[str, object]:
        calls.append(job_id)
        assert business_id == "business-1"
        if job_id == "job-2":
            raise ValueError("not awaiting review")
        return {"ok": True, "job_id": job_id, "status": "success", "post_url": "https://example.com/post"}

    monkeypatch.setattr(social_delivery_service, "confirm_social_job", fake_confirm)

    result = await social_delivery_service.confirm_social_jobs(
        object(), ["job-1", "job-2", "job-1"], business_id="business-1"
    )

    assert calls == ["job-1", "job-2"]
    assert result["status"] == "partial_failure"
    assert result["total"] == 2
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["items"][1]["error"] == "not awaiting review"


@pytest.mark.asyncio
async def test_confirm_social_jobs_rejects_empty_batch() -> None:
    with pytest.raises(ValueError, match="at least one"):
        await social_delivery_service.confirm_social_jobs(object(), [], business_id="business-1")


@pytest.mark.asyncio
async def test_confirm_social_job_preserves_executor_failure_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "id": "job-1",
        "business_id": "business-1",
        "container_code": "env-1",
        "executor_confirmation": "encrypted",
        "platform": "reddit",
    }
    recorded: dict[str, object] = {}

    async def fake_load(*_args: object, **_kwargs: object) -> dict[str, object]:
        return row

    class FakeCipher:
        def decrypt(self, _value: str) -> dict[str, str]:
            return {"confirmation_token": "token"}

    class FakeExecutor:
        async def command(self, _payload: dict[str, object]) -> dict[str, object]:
            return {
                "status": "manual_required",
                "reason_code": "platform_rejected",
                "message": "Reddit requires post flair",
            }

    async def fake_mark(
        _session: object,
        _row: object,
        error: str,
        *,
        evidence: dict[str, object] | None = None,
    ) -> None:
        recorded.update(error=error, evidence=evidence)

    monkeypatch.setattr(social_delivery_service, "_load_job", fake_load)
    monkeypatch.setattr(social_delivery_service, "_cipher", lambda: FakeCipher())
    monkeypatch.setattr(social_delivery_service, "_executor", lambda: FakeExecutor())
    monkeypatch.setattr(social_delivery_service, "_mark_manual", fake_mark)

    result = await social_delivery_service.confirm_social_job(
        object(), "job-1", business_id="business-1"
    )

    assert result["reason_code"] == "platform_rejected"
    assert result["error"] == "Reddit requires post flair"
    assert recorded["error"] == "Reddit requires post flair"
    assert recorded["evidence"] == {"executor": {
        "status": "manual_required",
        "reason_code": "platform_rejected",
        "message": "Reddit requires post flair",
    }}
