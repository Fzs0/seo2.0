"""Google 数据源配置存储。

启动时一次性从 config/google-data-sources.local.json 读入内存，提供：
  - list_sources()      : 全部 source（脱敏：不返回 private_key，只返回 fingerprint）
  - get_by_id(id)       : 按 source.id (UUID) 取完整配置（含 SA key）—— 仅后端客户端用
  - get_by_domain(host) : 按域名匹配（vapestest.de / https://vapestest.de 都行）
  - get_by_site_id(...) : 按 seo_agent.sites.id 反查：取 site.primary_domain 匹配 gscSiteUrl

设计：
  - 单例（与 app/engine/loader.py 的 RuleStore 风格一致）
  - 公开 API（list_sources）走 _public_source()，永远不返回 private_key
  - 内部 API（get_by_id）返回完整配置，但只允许同进程客户端调用
  - 文件不存在 / JSON 错时返回空配置（不抛异常，让前端用 "未配置" 占位）
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_CONFIG_PATH = Path(
    os.environ.get(
        "GOOGLE_DATA_SOURCES_PATH",
        str(Path(__file__).resolve().parents[2] / "config" / "google-data-sources.local.json"),
    )
)


@dataclass
class ServiceAccount:
    """GCP service account（整块 JSON）。完整字段在构造时存好。"""

    type: str
    project_id: str
    private_key_id: str
    private_key: str
    client_email: str
    client_id: str = ""
    auth_uri: str = "https://accounts.google.com/o/oauth2/auth"
    token_uri: str = "https://oauth2.googleapis.com/token"
    auth_provider_x509_cert_url: str = "https://www.googleapis.com/oauth2/v1/certs"
    client_x509_cert_url: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ServiceAccount":
        return cls(
            type=raw.get("type", "service_account"),
            project_id=raw["project_id"],
            private_key_id=raw.get("private_key_id", ""),
            private_key=raw.get("private_key", ""),
            client_email=raw.get("client_email", ""),
            client_id=raw.get("client_id", ""),
            auth_uri=raw.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
            token_uri=raw.get("token_uri", "https://oauth2.googleapis.com/token"),
            auth_provider_x509_cert_url=raw.get(
                "auth_provider_x509_cert_url",
                "https://www.googleapis.com/oauth2/v1/certs",
            ),
            client_x509_cert_url=raw.get("client_x509_cert_url", ""),
        )


@dataclass
class GoogleSource:
    """一个站点的 Google 数据源（GSC + GA4 + proxy）。"""

    id: str
    name: str
    gsc_site_url: str  # 例如 "https://exdivo.com/"
    ga4_property_id: str  # 例如 "534571138"
    google_proxy_url: str  # 例如 "http://127.0.0.1:7897"
    default_start_date: str
    default_end_date: str
    updated_at: str
    service_account: ServiceAccount
    ga4_hostnames: tuple[str, ...] = ()
    created_at: str = ""

    def gsc_host(self) -> str:
        """从 gsc_site_url 提取 host（用于域名匹配）。"""
        parsed = urlparse(self.gsc_site_url)
        return _normalize_hostname(parsed.netloc or parsed.path).removeprefix("www.")

    def ga4_hosts(self) -> tuple[str, ...]:
        """GA4 允许计入当前站点的 hostname；未显式配置时只认 GSC 主域名及 www。"""
        configured = _normalize_hostnames(self.ga4_hostnames)
        if configured:
            return configured
        host = self.gsc_host()
        return (host, f"www.{host}") if host else ()


class GoogleConfigStore:
    """Google 数据源配置的单例。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sources: list[GoogleSource] = []
        self._file_updated_at: str = ""
        self._load_error: str | None = None

    # ---------- public API ----------

    def reload(self) -> None:
        """重新读 config 文件。"""
        with self._lock:
            self._load_from_disk()

    def is_loaded(self) -> bool:
        return len(self._sources) > 0

    def load_error(self) -> str | None:
        return self._load_error

    def list_public(self) -> list[dict[str, Any]]:
        """返回脱敏后的 source 列表（前端用）：不含 private_key / private_key_id。"""
        return [
            {
                "id": s.id,
                "name": s.name,
                "gscSiteUrl": s.gsc_site_url,
                "ga4PropertyId": s.ga4_property_id,
                "ga4Hostnames": list(s.ga4_hosts()),
                "googleProxyUrl": s.google_proxy_url,
                "defaultStartDate": s.default_start_date,
                "defaultEndDate": s.default_end_date,
                "updatedAt": s.updated_at,
                "serviceAccountFingerprint": (s.service_account.client_email or "")[
                    :48
                ],
                "serviceAccountKeyId": (
                    s.service_account.private_key_id or ""
                )[:12],
            }
            for s in self._sources
        ]

    def get_by_id(self, source_id: str) -> GoogleSource | None:
        """按 source.id 取完整配置（含 SA key）。仅后端客户端用。"""
        for s in self._sources:
            if s.id == source_id:
                return s
        return None

    def get_by_domain(self, host_or_url: str) -> GoogleSource | None:
        """按域名匹配（大小写不敏感，自动去 www.）。"""
        if not host_or_url:
            return None
        # 既支持纯域名 "vapestest.de" 也支持 "https://vapestest.de/"
        if "://" in host_or_url:
            host = urlparse(host_or_url).netloc.lower().lstrip("www.")
        else:
            host = host_or_url.lower().lstrip("www.").rstrip("/")
        for s in self._sources:
            if s.gsc_host() == host:
                return s
        return None

    def list_all(self) -> list[GoogleSource]:
        """返回完整 sources 列表（仅后端客户端用）。"""
        return list(self._sources)

    # ---------- internals ----------

    def _load_from_disk(self) -> None:
        self._sources = []
        self._load_error = None
        if not _CONFIG_PATH.exists():
            self._load_error = f"config file not found: {_CONFIG_PATH}"
            return
        try:
            with _CONFIG_PATH.open("r", encoding="utf-8-sig") as f:
                raw = json.load(f)
        except Exception as e:  # noqa: BLE001
            self._load_error = f"failed to parse {_CONFIG_PATH}: {e}"
            return

        self._file_updated_at = raw.get("updatedAt", "")
        for entry in raw.get("sources", []):
            try:
                sa_raw = entry.get("serviceAccount", {})
                sa = ServiceAccount.from_dict(sa_raw)
                if not sa.project_id or not sa.client_email or not sa.private_key:
                    # 关键字段缺失，跳过这一条
                    continue
                self._sources.append(
                    GoogleSource(
                        id=entry["id"],
                        name=entry.get("name", ""),
                        gsc_site_url=entry["gscSiteUrl"],
                        ga4_property_id=str(entry.get("ga4PropertyId", "")),
                        google_proxy_url=entry.get("googleProxyUrl", ""),
                        default_start_date=entry.get("defaultStartDate", ""),
                        default_end_date=entry.get("defaultEndDate", ""),
                        updated_at=entry.get("updatedAt", ""),
                        created_at=entry.get("createdAt", ""),
                        service_account=sa,
                        ga4_hostnames=_normalize_hostnames(
                            entry.get("ga4Hostnames")
                        ),
                    )
                )
            except Exception:  # noqa: BLE001
                # 单条 source 损坏不影响其他
                continue


_store: GoogleConfigStore | None = None
_store_lock = threading.Lock()


def _normalize_hostname(value: Any) -> str:
    raw = str(value or "").strip().lower().rstrip(".")
    if "://" in raw:
        parsed = urlparse(raw)
        raw = parsed.hostname or ""
    return raw


def _normalize_hostnames(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set)):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        host = _normalize_hostname(value)
        if host and host not in seen:
            normalized.append(host)
            seen.add(host)
    return tuple(normalized)


def get_store() -> GoogleConfigStore:
    """单例 accessor。"""
    global _store
    with _store_lock:
        if _store is None:
            _store = GoogleConfigStore()
            _store.reload()
        return _store


def default_date_range(source: GoogleSource | None = None) -> tuple[str, str]:
    """返回默认日期区间（YYYY-MM-DD, YYYY-MM-DD）。

    优先级：
      1. 显式传 source + source.defaultStartDate/EndDate 非空 → 用配置
      2. 否则 → 过去 30 天 ~ 昨天
    """
    today = date.today()
    fallback_end = today.fromordinal(today.toordinal() - 1)
    fallback_start = today.fromordinal(today.toordinal() - 30)
    if source and source.default_start_date and source.default_end_date:
        return source.default_start_date, source.default_end_date
    return fallback_start.isoformat(), fallback_end.isoformat()
