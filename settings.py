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
    "version": 1,
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
            "kind": "openai_compatible",
            "enabled": False,
            "display_name": "GLM",
            "api_key": "",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "optional_quota_url": "",
            "optional_quota_json_path": "",
        },
        "MiniMax": {
            "id": "MiniMax",
            "kind": "openai_compatible",
            "enabled": False,
            "display_name": "MiniMax",
            "api_key": "",
            "base_url": "https://api.minimaxi.com/v1",
            "optional_quota_url": "",
            "optional_quota_json_path": "",
        },
    },
}


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def load_config() -> dict[str, Any]:
    """Load config from disk, falling back to defaults for any missing field."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if isinstance(data, dict):
            for pid, defaults in DEFAULT_CONFIG["providers"].items():
                user_block = (data.get("providers") or {}).get(pid) or {}
                merged = copy.deepcopy(defaults)
                if isinstance(user_block, dict):
                    merged.update(user_block)
                cfg["providers"][pid] = merged
            if isinstance(data.get("version"), int):
                cfg["version"] = data["version"]
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
