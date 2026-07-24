"""Canonical runtime contract for article QA data."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ArticleQaState(str, Enum):
    VALID = "valid"
    LEGACY = "legacy"
    MISSING = "missing"
    INVALID = "invalid"


class ArticleQaContractError(ValueError):
    """Raised when article QA cannot be represented by the storage contract."""


@dataclass(frozen=True)
class ArticleQaAssessment:
    checks: tuple[dict[str, Any], ...]
    summary: dict[str, Any]
    state: ArticleQaState
    passed: bool
    message: str | None = None

    @property
    def failed_keys(self) -> tuple[str, ...]:
        return tuple(str(check["key"]) for check in self.checks if check["ok"] is False)

    def require_storable(self) -> None:
        if self.state is ArticleQaState.INVALID:
            raise ArticleQaContractError(self.message or "QA 数据格式异常")


def assess_article_qa(
    raw: object,
    summary: object | None = None,
) -> ArticleQaAssessment:
    """Interpret canonical and known legacy QA shapes without trusting JSON types."""
    if summary is not None and not isinstance(summary, dict):
        return _invalid("QA summary 必须是对象")
    stored_summary = deepcopy(summary) if isinstance(summary, dict) else {}

    if raw is None or raw == []:
        if stored_summary.get("contract_state") == ArticleQaState.INVALID.value:
            return _invalid(
                str(stored_summary.get("contract_message") or "QA 数据格式异常"),
                summary=stored_summary,
            )
        return ArticleQaAssessment(
            checks=(),
            summary=stored_summary,
            state=ArticleQaState.MISSING,
            passed=False,
            message="未保存 QA 检查结果",
        )

    if isinstance(raw, list):
        checks, error = _canonical_checks(raw)
        if error:
            return _invalid(error, summary=stored_summary)
        state = (
            ArticleQaState.LEGACY
            if stored_summary.get("source_shape") == "legacy_envelope"
            else ArticleQaState.VALID
        )
        return _assessment_from_checks(checks, stored_summary, state)

    if isinstance(raw, dict):
        legacy_checks = raw.get("checks")
        if not isinstance(legacy_checks, dict) or not legacy_checks:
            return _invalid("QA 数据格式异常：对象缺少 checks 布尔映射")
        if any(not isinstance(key, str) or not key.strip() for key in legacy_checks):
            return _invalid("QA 数据格式异常：检查项 key 必须是非空字符串")
        if any(type(value) is not bool for value in legacy_checks.values()):
            return _invalid("QA 数据格式异常：legacy checks 的值必须是布尔值")

        legacy_summary = {
            str(key): deepcopy(value)
            for key, value in raw.items()
            if key != "checks"
        }
        legacy_summary.update(stored_summary)
        legacy_summary["source_shape"] = "legacy_envelope"
        checks = tuple(
            _legacy_check(key, value, legacy_summary)
            for key, value in sorted(legacy_checks.items())
        )
        derived_passed = bool(checks) and all(check["ok"] is True for check in checks)
        reported = raw.get("ok")
        if reported is not None and type(reported) is not bool:
            return _invalid(
                "QA 数据格式异常：legacy ok 必须是布尔值",
                checks=checks,
                summary=legacy_summary,
            )
        if type(reported) is bool and reported is not derived_passed:
            return _invalid(
                "QA 汇总结果与检查项冲突，已禁止发布",
                checks=checks,
                summary=legacy_summary,
            )
        return ArticleQaAssessment(
            checks=checks,
            summary=legacy_summary,
            state=ArticleQaState.LEGACY,
            passed=derived_passed,
            message="历史 QA 已转换为规范检查项",
        )

    return _invalid(f"QA 数据格式异常：不支持 {type(raw).__name__}")


def _canonical_checks(raw: list[object]) -> tuple[tuple[dict[str, Any], ...], str | None]:
    checks: list[dict[str, Any]] = []
    keys: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return (), f"QA 数据格式异常：第 {index + 1} 项不是对象"
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            return (), f"QA 数据格式异常：第 {index + 1} 项缺少非空 key"
        normalized_key = key.strip()
        if normalized_key in keys:
            return (), f"QA 数据格式异常：检查项 {normalized_key} 重复"
        if type(item.get("ok")) is not bool:
            return (), f"QA 数据格式异常：检查项 {normalized_key} 的 ok 必须是布尔值"
        keys.add(normalized_key)
        normalized = deepcopy(item)
        normalized["key"] = normalized_key
        normalized["ok"] = item["ok"]
        checks.append(normalized)
    return tuple(checks), None


def _legacy_check(
    key: str,
    ok: bool,
    summary: dict[str, Any],
) -> dict[str, Any]:
    check: dict[str, Any] = {"key": key.strip(), "ok": ok}
    value = summary.get(key)
    if value is not None and type(value) is not bool:
        check["value"] = deepcopy(value)
    return check


def _assessment_from_checks(
    checks: tuple[dict[str, Any], ...],
    summary: dict[str, Any],
    state: ArticleQaState,
) -> ArticleQaAssessment:
    if not checks:
        return ArticleQaAssessment(
            checks=(),
            summary=summary,
            state=ArticleQaState.MISSING,
            passed=False,
            message="未保存 QA 检查结果",
        )
    passed = all(check["ok"] is True for check in checks)
    reported = summary.get("ok")
    if "ok" in summary and type(reported) is not bool:
        return _invalid(
            "QA summary 的 ok 必须是布尔值",
            checks=checks,
            summary=summary,
        )
    if type(reported) is bool and reported is not passed:
        return _invalid(
            "QA 汇总结果与检查项冲突，已禁止发布",
            checks=checks,
            summary=summary,
        )
    return ArticleQaAssessment(
        checks=checks,
        summary=summary,
        state=state,
        passed=passed,
        message="历史 QA 已转换为规范检查项" if state is ArticleQaState.LEGACY else None,
    )


def _invalid(
    message: str,
    *,
    checks: tuple[dict[str, Any], ...] = (),
    summary: dict[str, Any] | None = None,
) -> ArticleQaAssessment:
    return ArticleQaAssessment(
        checks=checks,
        summary=summary or {},
        state=ArticleQaState.INVALID,
        passed=False,
        message=message,
    )
