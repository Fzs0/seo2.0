import pytest

from app.services.strategy_on_page_execution import (
    execute_strategy_on_page,
    preview_strategy_on_page,
)


class _MappingsResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _Session:
    def __init__(self, row):
        self.row = row
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return _MappingsResult(self.row)

    async def commit(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page_type", "expected_delegate"),
    [
        ("home", "home"),
        ("product", "product"),
        ("category", "category"),
    ],
)
async def test_on_page_strategy_preview_uses_existing_guarded_preview(
    monkeypatch, page_type, expected_delegate
):
    calls = []

    async def fake_home(session, connector_id, patch):
        calls.append(("home", connector_id, patch))
        return {"expected_snapshot_hash": "a" * 64, "changes": [{"field": "meta_title"}]}

    async def fake_product(session, connector_id, object_id, patch):
        calls.append(("product", connector_id, object_id, patch))
        return {"expected_snapshot_hash": "b" * 64, "changes": [{"field": "meta_title"}]}

    async def fake_category(session, connector_id, object_id, patch):
        calls.append(("category", connector_id, object_id, patch))
        return {"expected_snapshot_hash": "c" * 64, "changes": [{"field": "meta_title"}]}

    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.preview_home_seo_update", fake_home
    )
    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.preview_oemapps_seo_update", fake_product
    )
    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.preview_collection_seo_update", fake_category
    )
    session = _Session(
        {
            "strategy_type": "on_page_fix",
            "page_type": page_type,
            "connector_id": "connector-1",
            "external_id": "object-1",
            "site_status": "active",
            "strategy_enabled": True,
        }
    )

    result = await preview_strategy_on_page(
        session,
        strategy_task_id="task-1",
        patch={"meta_title": "Better title"},
        generation_mode="manual",
    )

    assert result["status"] == "previewed"
    assert result["page_type"] == page_type
    assert result["expected_snapshot_hash"] in {"a" * 64, "b" * 64, "c" * 64}
    assert calls[0][0] == expected_delegate


@pytest.mark.asyncio
async def test_on_page_execute_failure_is_never_reported_as_success(monkeypatch):
    async def fail(*args, **kwargs):
        raise ValueError("remote readback mismatch")

    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.execute_oemapps_seo_update", fail
    )
    session = _Session(
        {
            "strategy_type": "on_page_fix",
            "page_type": "product",
            "connector_id": "connector-1",
            "external_id": "product-1",
            "site_status": "active",
            "strategy_enabled": True,
            "target_url": "https://example.com/products/product-1",
            "base_url": "https://example.com",
        }
    )

    with pytest.raises(ValueError, match="remote readback mismatch"):
        await execute_strategy_on_page(
            session,
            strategy_task_id="task-1",
            patch={"meta_title": "Better title"},
            expected_snapshot_hash="a" * 64,
            confirm=True,
            confirm_variant_recreation=True,
            generation_mode="manual",
        )


@pytest.mark.asyncio
async def test_on_page_strategy_without_safe_connector_returns_explicit_blocker():
    session = _Session(
        {
            "strategy_type": "on_page_fix",
            "page_type": "product",
            "connector_id": None,
            "external_id": "product-1",
            "site_status": "active",
            "strategy_enabled": True,
            "target_url": "https://example.com/products/product-1",
            "base_url": "https://example.com",
        }
    )

    result = await preview_strategy_on_page(
        session, strategy_task_id="task-1", patch={"meta_title": "Better title"}
    )

    assert result["status"] == "blocked"
    assert result["blocker"] == "safe_connector_unavailable"


@pytest.mark.asyncio
async def test_internal_link_fix_is_blocked_when_no_safe_writer_exists():
    session = _Session(
        {
            "strategy_type": "on_page_fix",
            "page_type": "product",
            "subtype": "internal_link",
            "connector_id": "connector-1",
            "external_id": "product-1",
            "site_status": "active",
            "strategy_enabled": True,
        }
    )

    result = await preview_strategy_on_page(
        session, strategy_task_id="task-1", patch={"meta_title": "Unrelated patch"}
    )

    assert result["status"] == "blocked"
    assert result["blocker"] == "safe_writer_unavailable"


@pytest.mark.asyncio
async def test_on_page_strategy_rejects_non_seo_business_fields():
    session = _Session(
        {
            "strategy_type": "on_page_fix",
            "page_type": "product",
            "connector_id": "connector-1",
            "external_id": "product-1",
            "site_status": "active",
            "strategy_enabled": True,
        }
    )

    with pytest.raises(ValueError, match="unsupported on-page fields"):
        await preview_strategy_on_page(
            session,
            strategy_task_id="task-1",
            patch={"price": 1, "inventory": 0},
        )


@pytest.mark.asyncio
async def test_product_execution_requires_specific_variant_confirmation():
    session = _Session(
        {
            "strategy_type": "on_page_fix",
            "page_type": "product",
            "connector_id": "connector-1",
            "external_id": "product-1",
            "site_status": "active",
            "strategy_enabled": True,
            "target_url": "https://example.com/products/product-1",
            "base_url": "https://example.com",
        }
    )

    with pytest.raises(ValueError, match="variant recreation"):
        await execute_strategy_on_page(
            session,
            strategy_task_id="task-1",
            patch={"meta_title": "Better title"},
            expected_snapshot_hash="a" * 64,
            confirm=True,
            generation_mode="manual",
        )


def _approved_target(**overrides):
    return {
        "strategy_type": "on_page_fix",
        "page_type": "home",
        "connector_id": "connector-1",
        "external_id": None,
        "site_status": "active",
        "strategy_enabled": True,
        "target_url": "HTTP://WWW.EXAMPLE.COM/?utm_source=x#hero",
        "base_url": "https://example.com",
        "domain": "example.com",
        "decision": {
            "strategy_type": "on_page_fix",
            "page_type": "home",
            "target_url": "HTTP://WWW.EXAMPLE.COM/?utm_source=x#hero",
            "generation_mode": "model",
            "generation_provider": "openai",
            "generation_model": "gpt-5.6-sol",
            "generation_run_id": "generation-run-1",
            "on_page_preview": {
                "expected_snapshot_hash": "a" * 64,
                "home_seo": {"meta_title": "Before"},
            },
        },
        **overrides,
    }


@pytest.mark.asyncio
async def test_successful_on_page_readback_completes_original_strategy(monkeypatch):
    async def succeed(*args, **kwargs):
        return {
            "ok": True,
            "run_id": "run-1",
            "changes": [{"field": "meta_title"}],
            "home_seo": {"meta_title": "After"},
            "verification_errors": [],
        }

    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.execute_home_seo_update", succeed
    )
    session = _Session(_approved_target())

    result = await execute_strategy_on_page(
        session,
        strategy_task_id="task-1",
        patch={"meta_title": "After"},
        expected_snapshot_hash="a" * 64,
        confirm=True,
    )

    assert result["status"] == "executed"
    update = next(
        params for sql, params in session.statements if "execution_status" in sql
    )
    decision = __import__("json").loads(update["decision"])
    assert decision["execution_status"] == "completed"
    assert decision["operation_target_url"] == "https://example.com/"
    assert decision["submitted_patch"] == {"meta_title": "After"}
    assert decision["before_snapshot"]["home_seo"]["meta_title"] == "Before"
    assert decision["readback"]["home_seo"]["meta_title"] == "After"
    assert decision["execution_provider"] == "openai"
    assert decision["execution_model"] == "gpt-5.6-sol"
    assert decision["generation_run_id"] == "generation-run-1"


@pytest.mark.asyncio
async def test_failed_on_page_write_marks_original_strategy_failed(monkeypatch):
    async def fail(*args, **kwargs):
        raise ValueError("remote PUT failed")

    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.execute_home_seo_update", fail
    )
    session = _Session(_approved_target())

    with pytest.raises(ValueError, match="remote PUT failed"):
        await execute_strategy_on_page(
            session,
            strategy_task_id="task-1",
            patch={"meta_title": "After"},
            expected_snapshot_hash="a" * 64,
            confirm=True,
        )

    update = next(
        params for sql, params in session.statements if "execution_status" in sql
    )
    decision = __import__("json").loads(update["decision"])
    assert decision["execution_status"] == "failed"
    assert decision["retryable"] is True
    assert "remote PUT failed" in decision["execution_error"]


@pytest.mark.asyncio
async def test_on_page_readback_mismatch_marks_original_strategy_failed(monkeypatch):
    async def mismatch(*args, **kwargs):
        return {
            "ok": False,
            "run_id": "run-2",
            "verification_errors": ["meta_title did not match after write"],
        }

    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.execute_home_seo_update", mismatch
    )
    session = _Session(_approved_target())

    with pytest.raises(ValueError, match="successful readback"):
        await execute_strategy_on_page(
            session,
            strategy_task_id="task-1",
            patch={"meta_title": "After"},
            expected_snapshot_hash="a" * 64,
            confirm=True,
        )

    update = next(
        params for sql, params in session.statements if "execution_status" in sql
    )
    decision = __import__("json").loads(update["decision"])
    assert decision["execution_status"] == "failed"
    assert decision["api_result"]["verification_errors"]


@pytest.mark.asyncio
async def test_on_page_execution_rejects_cross_site_target_before_put(monkeypatch):
    called = False

    async def should_not_run(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.execute_home_seo_update",
        should_not_run,
    )
    session = _Session(
        _approved_target(
            target_url="https://other.example/",
            decision={
                **_approved_target()["decision"],
                "target_url": "https://other.example/",
            },
        )
    )

    with pytest.raises(ValueError, match="does not belong"):
        await execute_strategy_on_page(
            session,
            strategy_task_id="task-1",
            patch={"meta_title": "After"},
            expected_snapshot_hash="a" * 64,
            confirm=True,
        )

    assert called is False


@pytest.mark.asyncio
async def test_model_generated_preview_persists_exact_generation_provenance(monkeypatch):
    async def preview(*args, **kwargs):
        return {"expected_snapshot_hash": "a" * 64, "home_seo": {"meta_title": "Before"}}

    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.preview_home_seo_update", preview
    )
    session = _Session(
        _approved_target(
            decision={
                "strategy_type": "on_page_fix",
                "page_type": "home",
                "target_url": "https://example.com/",
            }
        )
    )

    await preview_strategy_on_page(
        session,
        strategy_task_id="task-1",
        patch={"meta_title": "Generated title"},
        generation_mode="model",
        generation_provider="openai",
        generation_model="gpt-5.6-sol",
        generation_run_id="run-123",
    )

    update = next(params for sql, params in session.statements if "execution_status" in sql)
    decision = __import__("json").loads(update["decision"])
    assert decision["generation_mode"] == "model"
    assert decision["generation_provider"] == "openai"
    assert decision["generation_model"] == "gpt-5.6-sol"
    assert decision["generation_run_id"] == "run-123"


@pytest.mark.asyncio
async def test_manual_on_page_patch_records_manual_mode_and_null_model(monkeypatch):
    async def preview(*args, **kwargs):
        return {"expected_snapshot_hash": "a" * 64, "home_seo": {"meta_title": "Before"}}

    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.preview_home_seo_update", preview
    )
    session = _Session(
        _approved_target(
            decision={
                "strategy_type": "on_page_fix",
                "page_type": "home",
                "target_url": "https://example.com/",
            }
        )
    )

    await preview_strategy_on_page(
        session,
        strategy_task_id="task-1",
        patch={"meta_title": "Human title"},
        generation_mode="manual",
    )

    update = next(params for sql, params in session.statements if "execution_status" in sql)
    decision = __import__("json").loads(update["decision"])
    assert decision["generation_mode"] == "manual"
    assert decision["generation_provider"] is None
    assert decision["generation_model"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        None,
        "",
        "unknown",
        "not_exposed_by_runtime",
        "gpt",
        "gpt-4",
        "gpt-5",
        "gpt-5.0",
        "latest",
        "default",
        "model",
        "auto",
    ],
)
async def test_model_generated_patch_without_exact_model_is_blocked_before_put(
    monkeypatch, model
):
    called = False

    async def preview(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(
        "app.services.strategy_on_page_execution.preview_home_seo_update", preview
    )
    session = _Session(
        _approved_target(
            decision={
                "strategy_type": "on_page_fix",
                "page_type": "home",
                "target_url": "https://example.com/",
                "generation_mode": "model",
                "generation_provider": "openai",
            }
        )
    )

    with pytest.raises(ValueError, match="exact generation model"):
        await preview_strategy_on_page(
            session,
            strategy_task_id="task-1",
            patch={"meta_title": "Generated title"},
            generation_mode="model",
            generation_provider="openai",
            generation_model=model,
        )

    assert called is False
