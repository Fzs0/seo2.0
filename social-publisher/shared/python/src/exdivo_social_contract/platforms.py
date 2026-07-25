"""Canonical platform capabilities and validation for social publishing."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
from urllib.parse import urlsplit


_PLATFORMS: dict[str, dict[str, Any]] = {
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
    "quora": {
        "display_name": "Quora",
        "content_types": ["answer_post"],
        "delivery_channel": "hubstudio_browser",
        "publish_adapter": "quora_web_post",
        "default_publish_url": "https://www.quora.com/",
        "allowed_hosts": ["quora.com", "www.quora.com"],
        "final_publish_mode": "explicit_human_confirmation",
        "requirements": ["logged_in_hubstudio_environment"],
        "policy_notes": ["Publish useful educational content and avoid undisclosed promotional claims."],
    },
    "tiktok": {
        "display_name": "TikTok",
        "content_types": ["short_video"],
        "delivery_channel": "hubstudio_browser",
        "publish_adapter": "tiktok_studio_upload",
        "default_publish_url": "https://www.tiktok.com/tiktokstudio/upload",
        "allowed_hosts": ["tiktok.com", "www.tiktok.com"],
        "final_publish_mode": "explicit_human_confirmation",
        "requirements": ["logged_in_hubstudio_environment", "local_video_file"],
        "policy_notes": ["The uploaded video and caption must comply with TikTok rules and local law."],
    },
    "youtube": {
        "display_name": "YouTube",
        "content_types": ["video"],
        "delivery_channel": "hubstudio_browser",
        "publish_adapter": "youtube_studio_upload",
        "default_publish_url": "https://www.youtube.com/upload",
        "allowed_hosts": ["youtube.com", "www.youtube.com", "studio.youtube.com", "youtu.be"],
        "final_publish_mode": "explicit_human_confirmation",
        "requirements": ["logged_in_hubstudio_environment", "local_video_file"],
        "policy_notes": ["The user confirms the final visibility and publish action."],
    },
    "instagram": {
        "display_name": "Instagram",
        "content_types": ["reel"],
        "delivery_channel": "hubstudio_browser",
        "publish_adapter": "instagram_reel_upload",
        "default_publish_url": "https://www.instagram.com/",
        "allowed_hosts": ["instagram.com", "www.instagram.com"],
        "final_publish_mode": "explicit_human_confirmation",
        "requirements": ["logged_in_hubstudio_environment", "local_video_file"],
        "policy_notes": ["The user confirms the final share action."],
    },
    "facebook": {
        "display_name": "Facebook",
        "content_types": ["video"],
        "delivery_channel": "hubstudio_browser",
        "publish_adapter": "facebook_video_upload",
        "default_publish_url": "https://www.facebook.com/",
        "allowed_hosts": ["facebook.com", "www.facebook.com", "m.facebook.com"],
        "final_publish_mode": "explicit_human_confirmation",
        "requirements": ["logged_in_hubstudio_environment", "local_video_file"],
        "policy_notes": ["The user confirms the final post action."],
    },
}

# Compatibility snapshot for callers that historically imported a plain dict.
# Validation never reads this public object, so callers cannot mutate canonical
# policy by changing nested lists or mappings here.
PLATFORMS: dict[str, dict[str, Any]] = deepcopy(_PLATFORMS)


def list_platform_specs() -> list[dict[str, Any]]:
    return [{"platform": key, **deepcopy(value)} for key, value in _PLATFORMS.items()]


def platform_spec(platform: str) -> dict[str, Any]:
    key = platform.strip().casefold()
    if key not in _PLATFORMS:
        raise ValueError(f"unsupported social platform: {platform}")
    return {"platform": key, **deepcopy(_PLATFORMS[key])}


def validate_package_payload(platform: str, content_type: str, payload: dict[str, Any]) -> None:
    spec = platform_spec(platform)
    platform = spec["platform"]
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
    if platform == "quora" and not body:
        raise ValueError("Quora packages require body content")
    if platform in {"tiktok", "youtube", "instagram", "facebook"}:
        if not body:
            raise ValueError(f"{spec['display_name']} packages require caption content")
        media = payload.get("media")
        if not isinstance(media, list) or len(media) != 1:
            raise ValueError(f"{spec['display_name']} packages require exactly one video")
        media_path = str((media[0] or {}).get("path") or "") if isinstance(media[0], dict) else ""
        if not media_path:
            raise ValueError(f"{spec['display_name']} media[0].path is required")
    if platform == "youtube" and (not title or len(title) > 100):
        raise ValueError("YouTube packages require a title of at most 100 characters")


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
    if not str(payload.get("container_code") or "").strip():
        raise ValueError(f"{platform} browser binding requires a Hubstudio container_code")


def validate_connection_config(platform: str, config: dict[str, Any]) -> dict[str, Any]:
    key = platform_spec(platform)["platform"]
    unknown = set(config) - {"base_url"}
    if unknown:
        raise ValueError(f"unsupported {key} connection config fields: {', '.join(sorted(unknown))}")
    base_url = str(config.get("base_url") or "http://127.0.0.1:6873").rstrip("/")
    if base_url != "http://127.0.0.1:6873":
        raise ValueError("Hubstudio connection must use http://127.0.0.1:6873")
    return {"base_url": base_url}


def validate_connection_secrets(platform: str, secrets: dict[str, str]) -> None:
    key = platform_spec(platform)["platform"]
    allowed = {"app_id", "app_secret", "group_code"}
    unknown = set(secrets) - allowed
    if unknown:
        raise ValueError(f"unsupported {key} secret fields: {', '.join(sorted(unknown))}")
    supplied = [bool(secrets.get(name)) for name in ("app_id", "app_secret", "group_code")]
    if any(supplied) and not all(supplied):
        raise ValueError("Hubstudio app_id, app_secret and group_code must be provided together")
