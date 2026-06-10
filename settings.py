"""Configuration storage for ailimit.

Config lives at ~/.ailimit/config.json. Keys never enter the repo.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
from typing import Any

CONFIG_DIR = pathlib.Path.home() / ".ailimit"
CONFIG_PATH = CONFIG_DIR / "config.json"

# Shape: each provider has its own block. `id` is the lookup key.
DEFAULT_CONFIG: dict[str, Any] = {
    "version": 2,
    "providers": {
        "codex": {
            "id": "codex",
            "kind": "codex",
            "enabled": True,
            "display_name": "Codex",
            # Codex is cookie-only by design; app-server fallback is disabled
            # so a stale 5h window is never re-armed.
            "use_app_server_fallback": False,
        },
        "glm": {
            "id": "glm",
            "kind": "glm",
            "enabled": False,
            "display_name": "GLM",
            "api_key": "",
            "base_url": "https://api.z.ai",
        },
        "minimax": {
            "id": "minimax",
            "kind": "minimax",
            "enabled": False,
            "display_name": "MiniMax",
            "api_key": "",
            "base_url": "https://api.minimaxi.com",
        },
    },
}

# Old provider ids that should fold into a current id on load.
LEGACY_PROVIDER_ALIASES = {"MiniMax": "minimax"}


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def _migrate_legacy_providers(raw_providers: dict[str, Any]) -> dict[str, Any]:
    """Fold legacy provider ids into their current names.

    If the user's old config still has e.g. `MiniMax`, copy its user-set
    fields onto `minimax` (current id takes priority for overlapping fields).
    Returns a new dict with legacy keys removed.
    """
    out = dict(raw_providers)
    for legacy, current in LEGACY_PROVIDER_ALIASES.items():
        if legacy not in out:
            continue
        legacy_block = out.pop(legacy)
        if not isinstance(legacy_block, dict):
            continue
        current_block = out.get(current) or {}
        if not isinstance(current_block, dict):
            current_block = {}
        # Only carry over fields the current block doesn't already have set.
        migrated = {}
        for k, v in legacy_block.items():
            if k in ("id", "kind"):
                continue
            if k in current_block and current_block[k] not in ("", None):
                continue
            migrated[k] = v
        merged = {**migrated, **current_block}
        out[current] = merged
    return out


def load_config() -> dict[str, Any]:
    """Load config from disk, falling back to defaults for any missing field.

    Unknown fields are dropped (so removed fields like optional_quota_url
    don't linger). When the saved version is older than DEFAULT_CONFIG's,
    `base_url` is reset to the current default for migrated providers,
    because the v1 default URLs no longer apply to the new providers.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if not CONFIG_PATH.exists():
        return cfg
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return cfg
    if not isinstance(data, dict):
        return cfg

    raw_providers = data.get("providers") or {}
    if isinstance(raw_providers, dict):
        raw_providers = _migrate_legacy_providers(raw_providers)
    else:
        raw_providers = {}

    saved_version = data.get("version") if isinstance(data.get("version"), int) else 0
    needs_url_refresh = saved_version < DEFAULT_CONFIG["version"]

    for pid, defaults in DEFAULT_CONFIG["providers"].items():
        user_block = raw_providers.get(pid) or {}
        merged = copy.deepcopy(defaults)
        if isinstance(user_block, dict):
            for k, v in user_block.items():
                if k not in merged:
                    continue  # drop fields no longer in the schema
                if k == "kind":
                    continue  # always keep the canonical kind
                if k == "base_url" and needs_url_refresh and defaults["kind"] in ("glm", "minimax"):
                    continue  # let the new default URL win after a major-version bump
                merged[k] = v
        cfg["providers"][pid] = merged

    cfg["version"] = max(saved_version, DEFAULT_CONFIG["version"])
    return cfg


def save_config(cfg: dict[str, Any]) -> pathlib.Path:
    """Write config; restrict permissions so api keys stay readable only by user."""
    _ensure_dir()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return CONFIG_PATH


def update_provider(provider_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Patch one provider block and persist. Returns the updated provider dict."""
    cfg = load_config()
    if provider_id in LEGACY_PROVIDER_ALIASES:
        provider_id = LEGACY_PROVIDER_ALIASES[provider_id]
    if provider_id not in cfg["providers"]:
        raise KeyError(f"unknown provider: {provider_id}")
    block = cfg["providers"][provider_id]
    for k, v in fields.items():
        if k not in block:
            raise KeyError(f"provider {provider_id} has no field {k}")
        # Coerce booleans coming from form posts.
        if isinstance(block[k], bool) and not isinstance(v, bool):
            v = str(v).lower() in ("1", "true", "yes", "on")
        block[k] = v
    save_config(cfg)
    return block


def redact(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with api_key values masked, for display."""
    out = copy.deepcopy(cfg)
    for block in out.get("providers", {}).values():
        key = block.get("api_key")
        if isinstance(key, str) and key:
            block["api_key"] = key[:4] + "…" + key[-2:] if len(key) > 6 else "***"
    return out
