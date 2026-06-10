"""macOS menu bar app for ailimit (rumps).

Calls `providers.check_all()` on the same config the CLI and Web UI use.
The status bar title is a compact summary like `AI  C:84% G:72% M:91%`;
the dropdown shows the full auth/quota/detail/error lines per provider.
Refresh is manual via "Refresh Now" and automatic every 5 minutes.

The rumps import is guarded so the helper functions (`build_title`,
`_short_for_status`) stay unit-testable on machines where rumps isn't
installed yet. `main()` is the only entry point that needs rumps.
"""
from __future__ import annotations

import re
import socket
import threading
import webbrowser
from typing import Optional

try:
    import rumps  # type: ignore
except ImportError:
    rumps = None  # type: ignore

from providers import ProviderStatus, check_all
from settings import load_config

REFRESH_SECONDS = 5 * 60
WEB_HOST = "127.0.0.1"
WEB_PORT = 8765

# Provider id -> menu bar tag. Only these three are tracked in the title;
# any future provider shows up in the dropdown but not the title.
_TITLE_TAGS = {"codex": "C", "glm": "G", "minimax": "M"}
_DROPDOWN_ORDER = ("codex", "glm", "minimax")


def _short_percent(detail: str) -> Optional[int]:
    """Extract the first integer percent from a quota_detail string.

    Detail formats we know about (all start with "5h: N.N% left"):
      GLM:      "5h: 84.0% left (reset 06-10 17:00), 7d: 95.0% left (reset 06-15 09:00)"
      MiniMax:  "5h: 84.0% left (in 2h15m), 7d: 95.0% left (in 5d3h), model: general"
      Codex:    "5h: 84.0% left, 7d: 95.0% left, plan: pro"
    Returns the integer 5h percent, clamped 0..100, or None if no match.
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*left", detail or "")
    if not m:
        return None
    try:
        return max(0, min(100, int(round(float(m.group(1))))))
    except ValueError:
        return None


def _short_for_status(st: ProviderStatus) -> str:
    """Single-char-or-percent marker for a provider in the title bar.

    Disabled / not_configured -> "-" (no live signal, no fake number).
    Live failure (auth=failed OR quota=unavailable/failed) -> "!".
    Live success -> "84%" style.
    Anything else -> "?" (shouldn't happen in practice).
    """
    if not st.enabled or st.auth_status == "disabled" or st.quota_status == "disabled":
        return "-"
    if st.auth_status == "not_configured" or st.quota_status == "not_configured":
        return "-"
    if st.quota_status == "ok" and st.auth_status == "ok":
        pct = _short_percent(st.quota_detail)
        return f"{pct}%" if pct is not None else "?"
    if st.auth_status == "failed" or st.quota_status in ("unavailable", "failed"):
        return "!"
    return "?"


def build_title(statuses: list[ProviderStatus]) -> str:
    """Build the menu bar title. `AI` prefix; `!` if any provider is in error."""
    parts: list[str] = []
    had_error = False
    for st in statuses:
        tag = _TITLE_TAGS.get(st.id)
        if not tag:
            continue
        if st.auth_status == "failed" or st.quota_status in ("unavailable", "failed"):
            had_error = True
        parts.append(f"{tag}:{_short_for_status(st)}")
    if not parts:
        return "AI -"
    prefix = "AI !" if had_error else "AI"
    return f"{prefix}  " + " ".join(parts)


def _menu_lines(st: ProviderStatus) -> list[str]:
    """Build the per-provider dropdown block (multi-line menu item title)."""
    lines = [f"{st.display_name} ({st.id})"]
    if not st.enabled:
        lines.append("  disabled")
    else:
        lines.append(
            f"  auth:   {st.auth_status}"
            + (f" — {st.auth_detail}" if st.auth_detail else "")
        )
        lines.append(
            f"  quota:  {st.quota_status}"
            + (f" — {st.quota_detail}" if st.quota_detail else "")
        )
        if st.quota_source:
            lines.append(f"  source: {st.quota_source}")
        if st.last_checked:
            lines.append(f"  checked: {st.last_checked}")
        if st.error:
            lines.append(f"  error:  {st.error}")
    return lines


def _port_in_use(host: str, port: int, timeout: float = 0.2) -> bool:
    """Return True if something is already listening on host:port."""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def _build_app_class():
    """Build the rumps App subclass only when rumps is importable."""
    if rumps is None:
        raise RuntimeError("rumps is not installed; run install.sh or 'pip install rumps'")

    class AilimitApp(rumps.App):
        def __init__(self) -> None:
            super().__init__("AI", quit_button=None)
            self.statuses: list[ProviderStatus] = []
            self._web_started = False

            # Per-provider dropdown blocks. Keep one MenuItem per provider and
            # rewrite its title on every refresh; this avoids menu churn.
            self._status_items = []
            for pid in _DROPDOWN_ORDER:
                item = rumps.MenuItem(f"loading {pid}…")
                self._status_items.append(item)
                self.menu.add(item)

            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem("Refresh Now", callback=self._cb_refresh))
            self.menu.add(rumps.MenuItem("Open Settings", callback=self._cb_open_settings))
            self.menu.add(rumps.MenuItem("Open Web UI", callback=self._cb_open_web))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

            # First refresh synchronously so the title is right on launch.
            self.refresh()
            # Subsequent refreshes on a timer.
            self._timer = rumps.Timer(self._cb_tick, REFRESH_SECONDS)
            self._timer.start()

        def _cb_tick(self, _sender) -> None:
            self.refresh()

        def refresh(self) -> None:
            try:
                cfg = load_config()
                self.statuses = check_all(cfg)
            except Exception as e:
                self.statuses = []
                self.title = "AI !"
                for item in self._status_items:
                    item.title = f"refresh error: {e}"
                return

            self.title = build_title(self.statuses)
            for item, st in zip(self._status_items, self.statuses):
                item.title = "\n".join(_menu_lines(st))

        def _cb_refresh(self, _sender) -> None:
            self.refresh()

        def _cb_open_settings(self, _sender) -> None:
            self._ensure_web()
            webbrowser.open(f"http://{WEB_HOST}:{WEB_PORT}/settings")

        def _cb_open_web(self, _sender) -> None:
            self._ensure_web()
            webbrowser.open(f"http://{WEB_HOST}:{WEB_PORT}/")

        def _ensure_web(self) -> None:
            if self._web_started:
                return
            self._web_started = True
            if _port_in_use(WEB_HOST, WEB_PORT):
                return  # someone else is serving; just open the URL
            try:
                import app  # local module, same install dir
                threading.Thread(
                    target=app.serve,
                    kwargs={"host": WEB_HOST, "port": WEB_PORT},
                    daemon=True,
                ).start()
            except Exception:
                # Don't loop trying — the URL still opens, just may not connect.
                self._web_started = False

    return AilimitApp


def main() -> int:
    if rumps is None:
        print(
            "rumps is not installed. Run install.sh, or:\n"
            "    pip install -r requirements.txt",
            flush=True,
        )
        return 1
    AilimitApp = _build_app_class()
    AilimitApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
