"""macOS menu bar app for ailimit (rumps + AppKit).

Calls `providers.check_all()` on the same config the CLI and Web UI use.
The status bar shows each provider as a short tag, the 5h percent, and an
SF Symbols battery image — e.g. `C 84% [battery.100]  G 72% [battery.75]  M 91% [battery.100]`.
Disabled / not_configured shows `C -`; live failure shows `C ⚠`. The dropdown
shows the full auth/quota/detail/error lines per provider. Refresh is manual
via "Refresh Now" and automatic every 5 minutes.

AppKit is used for the battery icons (NSImage + NSTextAttachment +
NSAttributedString on the NSStatusBar button). If AppKit isn't importable
(fresh pip install without PyObjC, or running off-macOS for tests), the
title falls back to a plain-text bar: `C 84% ▰▰▱▱`. The dropdown always
uses the rumps MenuItem text path.

The rumps and AppKit imports are both guarded so the helper functions
(`_short_percent`, `_native_bar`, `_build_items`, `build_title`) stay
unit-testable on machines without either installed.
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

try:
    import AppKit  # type: ignore
except ImportError:
    AppKit = None  # type: ignore

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
    """Single-char-or-percent marker for a provider in the title bar (legacy text mode)."""
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


# One bar item is (label, percent_or_none, state) where state is
#   "ok"    -> render label + percent + battery icon
#   "empty" -> render "label -" (disabled or not_configured; no fake number)
#   "error" -> render "label ⚠" (live call failed; no battery)
def _build_items(statuses: list[ProviderStatus]) -> list[tuple[str, Optional[int], str]]:
    items: list[tuple[str, Optional[int], str]] = []
    for st in statuses:
        tag = _TITLE_TAGS.get(st.id)
        if not tag:
            continue
        if not st.enabled or st.auth_status == "disabled" or st.quota_status == "disabled":
            items.append((tag, None, "empty"))
        elif st.auth_status == "not_configured" or st.quota_status == "not_configured":
            items.append((tag, None, "empty"))
        elif st.quota_status == "ok" and st.auth_status == "ok":
            pct = _short_percent(st.quota_detail)
            if pct is not None:
                items.append((tag, pct, "ok"))
            else:
                # We have "ok" status but couldn't parse a percent — surface as error.
                items.append((tag, None, "error"))
        else:
            items.append((tag, None, "error"))
    return items


def build_title(statuses: list[ProviderStatus]) -> str:
    """Legacy plain-text title builder. Still used for tests; the live menu
    bar uses `_native_bar_from_items` / `_set_bar_with_batteries` instead."""
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


# ---------------------------------------------------------------- text fallback
def _native_bar(pct: Optional[int], width: int = 4) -> str:
    """Unicode bar used when AppKit isn't available.

    pct=0   -> "▱▱▱▱"
    pct=50  -> "▰▰▱▱"   (2 filled)
    pct=100 -> "▰▰▰▰"
    None    -> ""
    """
    if pct is None:
        return ""
    pct = max(0, min(100, int(pct)))
    filled = round(pct / 100.0 * width)
    return "▰" * filled + "▱" * (width - filled)


def _native_bar_from_items(items: list[tuple[str, Optional[int], str]]) -> str:
    """Render the same items the AppKit path renders, but as plain text.

    e.g.  "C 84% ▰▰▱▱  G -  M ⚠"
    """
    parts: list[str] = []
    for label, pct, state in items:
        if state == "ok" and pct is not None:
            parts.append(f"{label} {pct}% {_native_bar(pct)}")
        elif state == "empty":
            parts.append(f"{label} -")
        else:  # "error"
            parts.append(f"{label} ⚠")
    return "  ".join(parts) if parts else "AI"


# ---------------------------------------------------------------- AppKit render
_BATTERY_TIERS = (
    (13, "battery.0"),
    (38, "battery.25"),
    (63, "battery.50"),
    (88, "battery.75"),
)


def _battery_symbol(pct: int) -> str:
    for threshold, name in _BATTERY_TIERS:
        if pct < threshold:
            return name
    return "battery.100"


def _sf_battery_image(pct: int, point_size: int = 14):
    """Return a template-mode NSImage for the percent, or None if AppKit missing.

    Applies an `NSImageSymbolConfiguration` so the symbol renders at a
    fixed point size + medium weight regardless of the system menu bar's
    current font size. Falls back to the bare image if configuration fails.
    """
    if AppKit is None:
        return None
    try:
        img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            _battery_symbol(pct), None
        )
    except Exception:
        return None
    if img is None:
        return None
    # Apply symbol configuration for consistent size + weight.
    try:
        cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            float(point_size), AppKit.NSFontWeightMedium
        )
        sized = img.imageWithSymbolConfiguration_(cfg)
        if sized is not None:
            img = sized
    except Exception:
        pass  # keep the unsized image rather than failing the whole render
    try:
        img.setTemplate_(True)
    except Exception:
        pass
    return img


def _battery_attachment(pct: int, font):
    """Wrap the SF battery image in an NSTextAttachment and align it to the font.

    `setBounds_` is required: without it, SF Symbols attachments can float
    at the attachment-cell's default size and look misaligned against the
    text baseline. We size the box to the image's natural size and shift
    it vertically so it centers on the font's cap height.
    """
    if AppKit is None:
        return None
    img = _sf_battery_image(pct, point_size=14)
    if img is None:
        return None
    attach = AppKit.NSTextAttachment.alloc().init()
    try:
        attach.setImage_(img)
    except Exception:
        return None
    if font is not None:
        try:
            cap = float(font.capHeight())
            try:
                sz = img.size()
                w = float(sz.width)
                h = float(sz.height)
            except Exception:
                w, h = 0.0, 0.0
            if w > 0 and h > 0:
                # Upstream ai-limit formula: shift the image so it centers on
                # the font's cap height. Positive when the symbol is smaller
                # than capHeight (pushes up toward the cap line), negative
                # when it's larger (lets it drop down to fit).
                y_offset = (cap - h) / 2.0
                attach.setBounds_(AppKit.NSMakeRect(0.0, y_offset, w, h))
        except Exception:
            pass
    return attach


def _ns_text(s: str, attrs: dict):
    """Build an NSAttributedString from a plain string + attributes dict.

    Uses the alloc/init form (`initWithString_attributes_`), which is the
    most reliably bridged across PyObjC versions; some class-factory
    shortcuts are not consistently available.
    """
    if AppKit is None:
        return None
    try:
        return AppKit.NSAttributedString.alloc().initWithString_attributes_(s, attrs)
    except Exception:
        return None


def _ns_attach(attach):
    """Wrap an NSTextAttachment in an NSAttributedString. Prefers the class
    factory form (matches upstream), falling back to alloc/init if the
    factory returns None or isn't bridged."""
    if AppKit is None or attach is None:
        return None
    try:
        s = AppKit.NSAttributedString.attributedStringWithAttachment_(attach)
        if s is not None:
            return s
    except Exception:
        pass
    try:
        return AppKit.NSAttributedString.alloc().initWithAttachment_(attach)
    except Exception:
        return None


def _render_attributed_title(items: list[tuple[str, Optional[int], str]]):
    """Build an NSAttributedString of the form `C 84% [battery]  G -  M ⚠`.

    Returns None if AppKit isn't available; callers should fall back to text.
    May raise — `_set_bar_with_batteries` wraps this in a try/except.
    """
    if AppKit is None:
        return None
    try:
        font = AppKit.NSFont.menuBarFontOfSize_(0)
    except Exception:
        font = None
    attrs: dict = {}
    if font is not None:
        attrs[AppKit.NSFontAttributeName] = font

    result = AppKit.NSMutableAttributedString.alloc().init()
    for i, (label, pct, state) in enumerate(items):
        prefix = "" if i == 0 else "  "
        if state == "ok" and pct is not None:
            text = _ns_text(f"{prefix}{label} {pct}% ", attrs)
            if text is not None:
                result.appendAttributedString_(text)
            attach = _battery_attachment(pct, font)
            attach_str = _ns_attach(attach)
            if attach_str is not None:
                result.appendAttributedString_(attach_str)
        elif state == "empty":
            text = _ns_text(f"{prefix}{label} -", attrs)
            if text is not None:
                result.appendAttributedString_(text)
        else:  # "error"
            text = _ns_text(f"{prefix}{label} ⚠", attrs)
            if text is not None:
                result.appendAttributedString_(text)
    return result


def _status_button(app):
    """Locate the rumps-managed NSStatusBar button. Returns the NSButton or None.

    Tries the rumps internals across versions, then as a last resort scans the
    app's attributes for anything exposing `.button()` that responds to
    `setAttributedTitle_` (i.e. an NSStatusBarButton-shaped thing).
    """
    if app is None:
        return None
    candidates: list = []
    for attr in ("_status_item", "_status_bar_item", "_nsstatusitem"):
        item = getattr(app, attr, None)
        if item is not None:
            candidates.append(item)
    nsapp = getattr(app, "_nsapp", None)
    if nsapp is not None:
        for inner_attr in ("nsstatusitem", "statusItem"):
            inner = getattr(nsapp, inner_attr, None)
            if inner is not None:
                candidates.append(inner)
    for item in candidates:
        b = getattr(item, "button", None)
        if callable(b):
            try:
                btn = b()
            except Exception:
                btn = None
            if btn is not None and hasattr(btn, "setAttributedTitle_"):
                return btn
        elif b is not None and hasattr(b, "setAttributedTitle_"):
            return b
    # Last resort: scan the app's attributes.
    for name in dir(app):
        if name.startswith("__"):
            continue
        try:
            val = getattr(app, name)
        except Exception:
            continue
        if val is None:
            continue
        b = getattr(val, "button", None)
        if not callable(b):
            continue
        try:
            btn = b()
        except Exception:
            continue
        if btn is not None and hasattr(btn, "setAttributedTitle_"):
            return btn
    return None


def _set_bar_with_batteries(app, items) -> bool:
    """Set the NSStatusBar button's attributed title to the battery-rendered string.

    Returns True on success; False on any AppKit failure (button missing,
    rendering exception, etc.) so callers can fall back to plain text.
    Any exception from `_render_attributed_title` is caught here — the
    refresh path must never crash.
    """
    if AppKit is None:
        return False
    button = _status_button(app)
    if button is None:
        return False
    try:
        attributed = _render_attributed_title(items)
    except Exception:
        return False
    if attributed is None:
        return False
    try:
        # Clear prior title/image so the new attributed title shows cleanly
        # (avoids a ghost SF Symbol on the left from a previous render).
        try:
            button.setImage_(None)
        except Exception:
            pass
        try:
            button.setTitle_("")
        except Exception:
            pass
        button.setAttributedTitle_(attributed)
    except Exception:
        return False
    return True


# ---------------------------------------------------------------- dropdown text
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

            items = _build_items(self.statuses)
            # Default path: AppKit SF Symbols battery icons in the menu bar.
            if not _set_bar_with_batteries(self, items):
                # Fallback: plain-text bar (no AppKit, or button not reachable).
                self.title = _native_bar_from_items(items)
            # Dropdown text always updated.
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
