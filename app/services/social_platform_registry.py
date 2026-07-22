"""Canonical capabilities and validation for the first social adapters."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


PLATFORMS: dict[str, dict[str, Any]] = {
    "x": {
        "display_name": "X",
        "content_types": ["short_post", "thread"],
        "delivery_channel": "hubstudio_browser",
        "publish_adapter": "x_web_intent",
        "default_publish_url": "https://twitter.com/intent/tweet",
        "allowed_hosts": ["twitter.com", "www.twitter.com", "x.com", "www.x.com"],
        "final_publish_mode": "explicit_human_confirmation",
        "requirements": ["logged_in_hubstudio_environment"],
        "policy_notes": [
            "Use the official Web Intent to prefill content; the user confirms the final publish action.",
        ],
    },
    "reddit": {
        "display_name": "Reddit",
        "content_types": ["discussion_post", "link_post"],
        "delivery_channel": "hubstudio_browser",
        "publish_adapter": "reddit_browser_review",
        "default_publish_url": "https://www.reddit.com/submit",
        "allowed_hosts": ["reddit.com", "www.reddit.com"],
        "final_publish_mode": "human_only",
        "requirements": ["logged_in_hubstudio_environment", "subreddit_rules_reviewed"],
        "policy_notes": [
            "Commercial API or Reddit data use may require Reddit approval or a separate agreement.",
            "Direct user-to-user transactions involving vaping products are prohibited.",
        ],
    },
}


def list_platform_specs() -> list[dict[str, Any]]:
    return [{"platform": key, **value} for key, value in PLATFORMS.items()]


def platform_spec(platform: str) -> dict[str, Any]:
    key = platform.strip().casefold()
    if key not in PLATFORMS:
        raise ValueError(f"unsupported social platform: {platform}")
    return {"platform": key, **PLATFORMS[key]}


def validate_package_payload(platform: str, content_type: str, payload: dict[str, Any]) -> None:
    spec = platform_spec(platform)
    if content_type not in spec["content_types"]:
        raise ValueError(f"unsupported {platform} content_type: {content_type}")
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if platform == "x":
        if title:
            raise ValueError("X packages must keep title empty")
        if content_type == "short_post" and len(body) > 280:
            raise ValueError("X short_post body exceeds the conservative 280-character limit")
        if content_type == "thread":
            posts = metadata.get("posts")
            if not isinstance(posts, list) or len(posts) < 2:
                raise ValueError("X thread metadata.posts must contain at least two posts")
            if any(not isinstance(item, str) or not item.strip() or len(item) > 280 for item in posts):
                raise ValueError("each X thread post must contain 1-280 characters")
    if platform == "reddit":
        if not title or len(title) > 300:
            raise ValueError("Reddit packages require a title of at most 300 characters")
        if not body:
            raise ValueError("Reddit packages require body content")
        subreddit = str(metadata.get("subreddit") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", subreddit):
            raise ValueError("Reddit metadata.subreddit is required and must be a valid subreddit name")
        if content_type == "link_post":
            target_url = str(metadata.get("target_url") or "")
            if urlsplit(target_url).scheme != "https":
                raise ValueError("Reddit link_post metadata.target_url must use https")


def validate_binding_payload(payload: dict[str, Any]) -> None:
    platform = str(payload.get("platform") or "").strip().casefold()
    spec = platform_spec(platform)
    if payload.get("delivery_channel") != spec["delivery_channel"]:
        raise ValueError(f"{platform} delivery_channel must be {spec['delivery_channel']}")
    if payload.get("publish_adapter") != spec["publish_adapter"]:
        raise ValueError(f"{platform} publish_adapter must be {spec['publish_adapter']}")
    parsed = urlsplit(str(payload.get("default_publish_url") or ""))
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in spec["allowed_hosts"]:
        raise ValueError(f"{platform} default_publish_url host is not allowed")
    if payload.get("enabled"):
        raise ValueError("new social bindings stay disabled until their real connection test succeeds")
    if platform in {"x", "reddit"} and not str(payload.get("container_code") or "").strip():
        raise ValueError(f"{platform} browser binding requires a Hubstudio container_code")


__all__ = [
    "PLATFORMS", "list_platform_specs", "platform_spec", "validate_binding_payload",
    "validate_package_payload",
]
