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

# Multimodal entries we ignore when picking a representative text model.
_MINIMAX_SKIP_MODELS = frozenset({"video", "audio", "image", "music", "speech"})


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


def _clamp_percent(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 100.0:
        return 100.0
    return f


def _fmt_reset(epoch: Any) -> str:
    """Format an epoch (or ISO string) as local "MM-DD HH:MM". Returns "" on bad input."""
    if epoch is None or epoch == "":
        return ""
    try:
        if isinstance(epoch, (int, float)):
            value = float(epoch)
            # GLM nextResetTime appears in seconds; if someone hands us ms, normalize.
            if value > 1e12:
                value = value / 1000.0
            dt = datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)
        else:
            dt = datetime.datetime.fromisoformat(str(epoch).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%m-%d %H:%M")
    except Exception:
        return str(epoch)


def _fmt_remains_seconds(secs: Any) -> str:
    try:
        s = int(secs)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def _get_json(url: str, headers: dict[str, str], timeout: int = REMOTE_TIMEOUT_SEC) -> Any:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body.decode("utf-8", errors="replace"))


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


# ----------------------------------------------------------------------- GLM
class GLMProvider:
    """Zhipu Coding Plan quota via api.z.ai/api/monitor/usage/quota/limit.

    Authorization is the raw key (no Bearer prefix).
    Success = HTTP 200 AND `success: true`.
    """

    DEFAULT_QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"

    def __init__(self, block: dict[str, Any]):
        self.block = block

    def check(self) -> ProviderStatus:
        st = ProviderStatus(
            id=self.block["id"],
            display_name=self.block.get("display_name") or "GLM",
            enabled=bool(self.block.get("enabled", False)),
            last_checked=_now_local_iso(),
        )
        if not st.enabled:
            st.auth_status = "disabled"
            st.quota_status = "disabled"
            return st

        key = (self.block.get("api_key") or "").strip()
        if not key or key == "***":
            st.auth_status = "not_configured"
            st.auth_detail = "api_key is empty or placeholder"
            st.quota_status = "not_configured"
            return st

        url = self.DEFAULT_QUOTA_URL
        st.quota_source = url
        headers = {
            "Authorization": key,        # NO Bearer prefix for GLM
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en",
            "User-Agent": "ailimit/0.1",
        }
        try:
            data = _get_json(url, headers)
        except urllib.error.HTTPError as e:
            st.auth_status = "failed" if e.code in (401, 403) else "ok"
            st.quota_status = "unavailable"
            st.error = f"HTTP {e.code}"
            st.auth_detail = f"GLM quota endpoint HTTP {e.code}"
            return st
        except Exception as e:
            st.auth_status = "failed"
            st.quota_status = "unavailable"
            st.error = str(e)
            return st

        if not isinstance(data, dict) or not data.get("success"):
            msg = (data or {}).get("message") if isinstance(data, dict) else None
            st.auth_status = "failed"
            st.quota_status = "unavailable"
            st.error = f"success!=true: {msg or 'unknown'}"
            return st

        limits_raw = ((data.get("data") or {}).get("limits")) or []
        token_limits = [
            l for l in limits_raw
            if isinstance(l, dict) and l.get("type") == "TOKENS_LIMIT"
        ]
        token_limits.sort(key=lambda l: l.get("nextResetTime") or 0)

        if not token_limits:
            st.auth_status = "ok"
            st.quota_status = "unavailable"
            st.quota_detail = "no TOKENS_LIMIT entries in response"
            st.extra["raw"] = data
            return st

        primary = token_limits[0]
        secondary = token_limits[1] if len(token_limits) >= 2 else None

        used_5h = _clamp_percent(primary.get("percentage", 0))
        remaining_5h = 100.0 - used_5h
        reset_5h = _fmt_reset(primary.get("nextResetTime"))
        parts = [f"5h: {remaining_5h:.1f}% left"
                 + (f" (reset {reset_5h})" if reset_5h else "")]

        if secondary is not None:
            used_7d = _clamp_percent(secondary.get("percentage", 0))
            remaining_7d = 100.0 - used_7d
            reset_7d = _fmt_reset(secondary.get("nextResetTime"))
            parts.append(f"7d: {remaining_7d:.1f}% left"
                         + (f" (reset {reset_7d})" if reset_7d else ""))
            st.extra["weekly"] = {"used_percent": used_7d,
                                  "resets_at": secondary.get("nextResetTime")}

        st.auth_status = "ok"
        st.auth_detail = f"{len(token_limits)} TOKENS_LIMIT entries"
        st.quota_status = "ok"
        st.quota_detail = ", ".join(parts)
        st.extra["primary"] = {"used_percent": used_5h,
                               "resets_at": primary.get("nextResetTime")}
        return st


# ------------------------------------------------------------------ MiniMax
class MiniMaxProvider:
    """MiniMax token_plan/remains quota.

    Bearer auth. HTTP 200 alone is not success — must check base_resp.status_code == 0.
    Picks `model_name == "general"` if present, else first non-multimodal entry.
    """

    DEFAULT_QUOTA_BASE = "https://api.minimaxi.com"
    GLOBAL_QUOTA_BASE = "https://api.minimax.io"
    QUOTA_PATH = "/v1/token_plan/remains"

    def __init__(self, block: dict[str, Any]):
        self.block = block

    def _quota_url(self) -> str:
        base_url = (self.block.get("base_url") or "").strip().rstrip("/")
        if "minimax.io" in base_url:
            return self.GLOBAL_QUOTA_BASE + self.QUOTA_PATH
        return self.DEFAULT_QUOTA_BASE + self.QUOTA_PATH

    def check(self) -> ProviderStatus:
        st = ProviderStatus(
            id=self.block["id"],
            display_name=self.block.get("display_name") or "MiniMax",
            enabled=bool(self.block.get("enabled", False)),
            last_checked=_now_local_iso(),
        )
        if not st.enabled:
            st.auth_status = "disabled"
            st.quota_status = "disabled"
            return st

        key = (self.block.get("api_key") or "").strip()
        if not key or key == "***":
            st.auth_status = "not_configured"
            st.auth_detail = "api_key is empty or placeholder"
            st.quota_status = "not_configured"
            return st

        url = self._quota_url()
        st.quota_source = url
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "ailimit/0.1",
        }
        try:
            data = _get_json(url, headers)
        except urllib.error.HTTPError as e:
            st.auth_status = "failed" if e.code in (401, 403) else "ok"
            st.quota_status = "unavailable"
            st.error = f"HTTP {e.code}"
            return st
        except Exception as e:
            st.auth_status = "failed"
            st.quota_status = "unavailable"
            st.error = str(e)
            return st

        if not isinstance(data, dict):
            st.auth_status = "failed"
            st.quota_status = "unavailable"
            st.error = "non-object response"
            return st

        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") != 0:
            code = base_resp.get("status_code")
            msg = base_resp.get("status_msg") or "unknown"
            st.auth_status = "failed"
            st.quota_status = "unavailable"
            st.error = f"base_resp {code}: {msg}"
            st.auth_detail = f"base_resp {code}: {msg}"
            return st

        remains = data.get("model_remains") or []
        if not isinstance(remains, list) or not remains:
            st.auth_status = "ok"
            st.quota_status = "unavailable"
            st.quota_detail = "model_remains is empty"
            st.extra["raw"] = data
            return st

        filtered = [r for r in remains
                    if isinstance(r, dict)
                    and r.get("model_name") not in _MINIMAX_SKIP_MODELS]
        if not filtered:
            st.auth_status = "ok"
            st.quota_status = "unavailable"
            st.quota_detail = "no text model entries (all multimodal)"
            return st

        chosen = next((r for r in filtered if r.get("model_name") == "general"),
                      filtered[0])
        model_name = chosen.get("model_name") or "?"

        def _used_from_remaining(rem_pct):
            if rem_pct is None:
                return None
            return _clamp_percent(100.0 - _clamp_percent(rem_pct))

        used_5h = _used_from_remaining(chosen.get("current_interval_remaining_percent"))
        if used_5h is None or used_5h == 0.0:
            total = chosen.get("current_interval_total_count")
            usage = chosen.get("current_interval_usage_count")
            try:
                if total and total > 0 and usage is not None:
                    fallback = round(100.0 * float(usage) / float(total), 1)
                    used_5h = _clamp_percent(fallback)
            except (TypeError, ValueError):
                pass
        if used_5h is None:
            used_5h = 0.0

        used_7d = _used_from_remaining(chosen.get("current_weekly_remaining_percent"))
        if used_7d is None:
            used_7d = 0.0

        remaining_5h = 100.0 - used_5h
        remaining_7d = 100.0 - used_7d

        parts = [f"5h: {remaining_5h:.1f}% left"]
        rt = _fmt_remains_seconds(chosen.get("remains_time"))
        if rt:
            parts[-1] += f" (in {rt})"
        parts.append(f"7d: {remaining_7d:.1f}% left")
        wt = _fmt_remains_seconds(chosen.get("weekly_remains_time"))
        if wt:
            parts[-1] += f" (in {wt})"
        parts.append(f"model: {model_name}")

        st.auth_status = "ok"
        st.auth_detail = f"{len(filtered)} text model(s) reachable"
        st.quota_status = "ok"
        st.quota_detail = ", ".join(parts)
        st.extra["primary"] = {"used_percent": used_5h,
                               "remains_time": chosen.get("remains_time")}
        st.extra["weekly"] = {"used_percent": used_7d,
                              "remains_time": chosen.get("weekly_remains_time")}
        st.extra["model_name"] = model_name
        return st


# ------------------------------------------------------- OpenAI-compatible
class OpenAICompatProvider:
    """Generic OpenAI-compatible probe, kept for future custom providers.

    Auth probe: GET {base_url}/models with Bearer key.
    No built-in quota path; quota is "not_configured" unless caller wires
    something up elsewhere.
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
        if not key or key == "***" or not base_url:
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
            n = len(data.get("data") or []) if isinstance(data, dict) else 0
            st.auth_status = "ok"
            st.auth_detail = f"{n} models reachable"
        except urllib.error.HTTPError as e:
            st.auth_status = "failed"
            st.auth_detail = f"/models HTTP {e.code}"
            st.error = f"{e.code} {e.reason}"
            st.quota_status = "unavailable"
            return st
        except Exception as e:
            st.auth_status = "failed"
            st.auth_detail = f"/models error: {e}"
            st.error = str(e)
            st.quota_status = "unavailable"
            return st

        st.quota_status = "not_configured"
        st.quota_detail = "generic provider has no built-in quota endpoint"
        return st


# ------------------------------------------------------------------ factory
def build_provider(block: dict[str, Any]):
    kind = block.get("kind")
    if kind == "codex":
        return CodexProvider(block)
    if kind == "glm":
        return GLMProvider(block)
    if kind == "minimax":
        return MiniMaxProvider(block)
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
