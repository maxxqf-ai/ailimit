"""Provider implementations for ailimit.

Each provider exposes `check()` -> ProviderStatus. The CLI and web UI
both render the same status objects, so adding a new provider only
requires extending this file and `settings.DEFAULT_CONFIG`.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import urllib.error
import urllib.request
from typing import Any, Optional


REMOTE_TIMEOUT_SEC = 15
_CHATGPT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclasses.dataclass
class ProviderStatus:
    id: str
    display_name: str
    enabled: bool
    auth_status: str = "unknown"        # ok | failed | not_configured | disabled
    auth_detail: str = ""
    quota_status: str = "unknown"       # ok | unavailable | not_configured | disabled
    quota_detail: str = ""
    quota_source: str = ""               # "chatgpt cookie" | "{url}" | "" | "local snapshot"
    last_checked: Optional[str] = None
    error: str = ""
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _now_local_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _get_json(url: str, headers: dict[str, str], timeout: int = REMOTE_TIMEOUT_SEC) -> Any:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body.decode("utf-8", errors="replace"))


def _walk_json_path(data: Any, path: str) -> Any:
    """Tiny dotted-path extractor: 'a.b.0.c' supports dict keys and list indexes."""
    cur = data
    if not path:
        return data
    for raw in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(raw)]
                continue
            except (ValueError, IndexError):
                return None
        if isinstance(cur, dict):
            cur = cur.get(raw)
            continue
        return None
    return cur


# --------------------------------------------------------------------- Codex
class CodexProvider:
    """Read-only chatgpt.com cookie path. No app-server fallback."""

    def __init__(self, block: dict[str, Any]):
        self.block = block

    def check(self) -> ProviderStatus:
        st = ProviderStatus(
            id=self.block["id"],
            display_name=self.block.get("display_name", "Codex"),
            enabled=bool(self.block.get("enabled", True)),
            last_checked=_now_local_iso(),
        )
        if not st.enabled:
            st.auth_status = "disabled"
            st.quota_status = "disabled"
            return st
        try:
            cookies = self._load_chatgpt_cookies()
        except Exception as e:
            st.auth_status = "failed"
            st.auth_detail = str(e)
            st.quota_status = "unavailable"
            st.error = str(e)
            return st

        cookie_header = "; ".join(f"{n}={v}" for n, v in cookies)
        try:
            token = self._get_access_token(cookie_header)
        except Exception as e:
            st.auth_status = "failed"
            st.auth_detail = str(e)
            st.quota_status = "unavailable"
            st.error = str(e)
            return st

        try:
            data = _get_json(
                "https://chatgpt.com/backend-api/codex/usage",
                self._headers(cookie_header, bearer=token),
            )
        except urllib.error.HTTPError as e:
            st.auth_status = "failed" if e.code in (401, 403) else "ok"
            st.quota_status = "unavailable"
            st.error = f"HTTP {e.code}"
            return st
        except Exception as e:
            st.auth_status = "ok"
            st.quota_status = "unavailable"
            st.error = str(e)
            return st

        st.auth_status = "ok"
        st.quota_status = "ok"
        st.quota_source = "chatgpt cookie"
        st.extra["rate_limit"] = self._normalize(data)
        primary = st.extra["rate_limit"].get("primary") or {}
        secondary = st.extra["rate_limit"].get("secondary") or {}

        def _fmt(win):
            if not win:
                return "-"
            used = win.get("used_percent", 0) or 0
            remaining = max(0.0, 100.0 - float(used))
            return f"{remaining:.1f}% left"

        st.quota_detail = (
            f"5h: {_fmt(primary)}, 7d: {_fmt(secondary)}, "
            f"plan: {st.extra['rate_limit'].get('plan_type') or '-'}"
        )
        return st

    def _load_chatgpt_cookies(self):
        try:
            import browser_cookie3  # type: ignore
        except ImportError as e:
            raise RuntimeError("browser_cookie3 not installed (pip install browser-cookie3)") from e
        errs = []
        for name, loader in [("Chrome", browser_cookie3.chrome),
                             ("Firefox", browser_cookie3.firefox)]:
            try:
                jar = loader(domain_name=".chatgpt.com")
                cookies = [(c.name, c.value) for c in jar]
                if cookies:
                    return cookies
            except Exception as e:
                errs.append(f"{name}: {e}")
        detail = f" ({'; '.join(errs)})" if errs else ""
        raise RuntimeError(
            f"cannot read chatgpt.com cookies{detail}; please log in to chatgpt.com in Chrome or Firefox"
        )

    def _headers(self, cookie_header: str, *, bearer: str | None = None) -> dict[str, str]:
        h = {
            "Cookie": cookie_header,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": _CHATGPT_UA,
            "Referer": "https://chatgpt.com/codex/cloud/settings/analytics",
            "Origin": "https://chatgpt.com",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if bearer:
            h["Authorization"] = f"Bearer {bearer}"
        return h

    def _get_access_token(self, cookie_header: str) -> str:
        data = _get_json("https://chatgpt.com/api/auth/session",
                         self._headers(cookie_header))
        token = data.get("accessToken")
        if not token:
            raise RuntimeError("no accessToken in session response (not signed in?)")
        return token

    @staticmethod
    def _normalize(data: dict) -> dict:
        rl = data.get("rate_limit") or {}

        def win(w):
            if not w:
                return None
            wsec = w.get("limit_window_seconds")
            return {
                "used_percent": w.get("used_percent", 0),
                "window_minutes": wsec // 60 if wsec else None,
                "resets_at": w.get("reset_at"),
            }

        return {
            "primary": win(rl.get("primary_window")),
            "secondary": win(rl.get("secondary_window")),
            "credits": data.get("credits"),
            "plan_type": data.get("plan_type"),
        }


# ------------------------------------------------------- OpenAI-compatible
class OpenAICompatProvider:
    """Used by GLM and MiniMax.

    Auth probe: GET ${base_url}/models with Bearer key.
    Optional quota probe: GET ${optional_quota_url} with same Bearer key,
    extract field via ${optional_quota_json_path} (dotted, supports list idx).
    """

    def __init__(self, block: dict[str, Any]):
        self.block = block

    def check(self) -> ProviderStatus:
        st = ProviderStatus(
            id=self.block["id"],
            display_name=self.block.get("display_name") or self.block["id"],
            enabled=bool(self.block.get("enabled", False)),
            last_checked=_now_local_iso(),
        )
        if not st.enabled:
            st.auth_status = "disabled"
            st.quota_status = "disabled"
            return st

        key = (self.block.get("api_key") or "").strip()
        base_url = (self.block.get("base_url") or "").strip().rstrip("/")
        if not key or not base_url:
            st.auth_status = "not_configured"
            st.auth_detail = "api_key and base_url required"
            st.quota_status = "not_configured"
            return st

        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "ailimit/0.1",
        }

        try:
            data = _get_json(f"{base_url}/models", headers)
            model_count = len(data.get("data") or []) if isinstance(data, dict) else 0
            st.auth_status = "ok"
            st.auth_detail = f"{model_count} models reachable"
        except urllib.error.HTTPError as e:
            st.auth_status = "failed"
            st.auth_detail = f"/models HTTP {e.code}"
            st.error = f"{e.code} {e.reason}"
        except Exception as e:
            st.auth_status = "failed"
            st.auth_detail = f"/models error: {e}"
            st.error = str(e)

        if st.auth_status != "ok":
            st.quota_status = "unavailable"
            return st

        quota_url = (self.block.get("optional_quota_url") or "").strip()
        if not quota_url:
            st.quota_status = "not_configured"
            st.quota_detail = "no optional_quota_url set; API key works but quota endpoint not configured"
            return st

        try:
            qdata = _get_json(quota_url, headers)
        except urllib.error.HTTPError as e:
            st.quota_status = "unavailable"
            st.quota_detail = f"quota_url HTTP {e.code}"
            st.error = (st.error + "; " if st.error else "") + f"quota HTTP {e.code}"
            return st
        except Exception as e:
            st.quota_status = "unavailable"
            st.quota_detail = f"quota_url error: {e}"
            st.error = (st.error + "; " if st.error else "") + str(e)
            return st

        st.quota_source = quota_url
        path = (self.block.get("optional_quota_json_path") or "").strip()
        if not path:
            st.quota_status = "ok"
            st.quota_detail = "quota_url responded (no json_path set; raw response in extra)"
            st.extra["quota_raw"] = qdata
            return st

        value = _walk_json_path(qdata, path)
        if value is None:
            st.quota_status = "unavailable"
            st.quota_detail = f"json_path {path!r} not found in response"
            st.extra["quota_raw"] = qdata
            return st

        st.quota_status = "ok"
        st.quota_detail = f"{path} = {value}"
        st.extra["quota_value"] = value
        return st


# ------------------------------------------------------------------ factory
def build_provider(block: dict[str, Any]):
    kind = block.get("kind")
    if kind == "codex":
        return CodexProvider(block)
    if kind == "openai_compatible":
        return OpenAICompatProvider(block)
    raise ValueError(f"unknown provider kind: {kind!r}")


def check_all(cfg: dict[str, Any]) -> list[ProviderStatus]:
    out = []
    for block in cfg.get("providers", {}).values():
        try:
            out.append(build_provider(block).check())
        except Exception as e:
            out.append(ProviderStatus(
                id=block.get("id", "?"),
                display_name=block.get("display_name", "?"),
                enabled=bool(block.get("enabled", False)),
                auth_status="failed",
                error=f"provider build/check crashed: {e}",
                last_checked=_now_local_iso(),
            ))
    return out
