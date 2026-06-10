"""Tiny stdlib http.server web UI for ailimit.

Two endpoints:
  GET  /            status of every provider
  GET  /settings    HTML form to edit each provider's fields
  POST /settings    persists the form

Kept dependency-free so this file can later be lifted into a rumps menu
bar app: providers.check_all() and settings.* are the only contracts.
"""
from __future__ import annotations

import html
import http.server
import json
import urllib.parse
from typing import Any

from providers import check_all
from settings import load_config, redact, save_config


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _status_html() -> str:
    cfg = load_config()
    statuses = check_all(cfg)
    rows = []
    for s in statuses:
        rows.append(f"""
        <tr>
          <td><b>{_esc(s.display_name)}</b><br><span class="dim">{_esc(s.id)}</span></td>
          <td>{_esc("on" if s.enabled else "off")}</td>
          <td>{_esc(s.auth_status)}<br><span class="dim">{_esc(s.auth_detail)}</span></td>
          <td>{_esc(s.quota_status)}<br><span class="dim">{_esc(s.quota_detail)}</span></td>
          <td><span class="dim">{_esc(s.quota_source)}</span></td>
          <td><span class="dim">{_esc(s.last_checked)}</span></td>
          <td>{_esc(s.error)}</td>
        </tr>""")
    table = "\n".join(rows)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>ailimit</title>
<style>
 body {{ font-family:-apple-system,sans-serif; margin:2rem; }}
 table {{ border-collapse:collapse; width:100%; }}
 th, td {{ border:1px solid #ddd; padding:8px; text-align:left; vertical-align:top; }}
 th {{ background:#f5f5f5; }}
 .dim {{ color:#777; font-size:0.85em; }}
 a.btn {{ display:inline-block; padding:6px 12px; background:#222; color:#fff;
          text-decoration:none; border-radius:4px; }}
</style></head><body>
<h1>ailimit</h1>
<p><a class="btn" href="/settings">Settings</a>
<a class="btn" href="/" style="margin-left:8px;background:#555">Refresh</a></p>
<table>
<tr><th>Provider</th><th>Enabled</th><th>Auth</th><th>Quota</th>
    <th>Source</th><th>Checked</th><th>Error</th></tr>
{table}
</table>
<p class="dim">GLM and MiniMax use built-in quota endpoints — the settings page only needs
enabled / api_key / base_url. For MiniMax, base_url picks the regional endpoint
(api.minimaxi.com by default, api.minimax.io if the URL contains "minimax.io").
api_key empty or "***" shows "not_configured" and skips the live call — never a fake number.</p>
</body></html>"""


def _settings_html(message: str = "") -> str:
    cfg = load_config()
    blocks = []
    for pid, block in cfg["providers"].items():
        rows = []
        for field, value in block.items():
            if field in ("id", "kind"):
                continue
            field_id = f"{pid}__{field}"
            if isinstance(value, bool):
                checked = " checked" if value else ""
                inp = (f'<input type="hidden" name="{field_id}" value="false">'
                       f'<input type="checkbox" id="{field_id}" name="{field_id}"'
                       f' value="true"{checked}>')
            else:
                kind = "password" if field == "api_key" else "text"
                inp = (f'<input type="{kind}" id="{field_id}" name="{field_id}"'
                       f' value="{_esc(value)}" style="width:32rem">')
            rows.append(
                f'<tr><td><label for="{field_id}">{_esc(field)}</label></td>'
                f'<td>{inp}</td></tr>'
            )
        blocks.append(
            f'<fieldset><legend>{_esc(block.get("display_name") or pid)} '
            f'<span class="dim">({_esc(pid)} / {_esc(block.get("kind"))})</span></legend>'
            f'<table>{"".join(rows)}</table></fieldset>'
        )
    blocks_html = "\n".join(blocks)
    msg_html = (f'<p style="color:green">{_esc(message)}</p>' if message else "")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>ailimit settings</title>
<style>
 body {{ font-family:-apple-system,sans-serif; margin:2rem; max-width:60rem; }}
 fieldset {{ margin-bottom:1.5rem; padding:1rem; }}
 table {{ border-collapse:collapse; width:100%; }}
 td {{ padding:6px 4px; vertical-align:top; }}
 td:first-child {{ width:14rem; font-family:monospace; }}
 .dim {{ color:#777; font-size:0.85em; }}
 button {{ padding:8px 16px; }}
</style></head><body>
<h1>ailimit settings</h1>
<p><a href="/">&larr; back to status</a></p>
{msg_html}
<form method="post" action="/settings">
{blocks_html}
<button type="submit">Save</button>
</form>
<p class="dim">Saved to ~/.ailimit/config.json (chmod 600). Keys never enter git.</p>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ailimit/0.1"

    def log_message(self, fmt, *args):  # quieter default
        return

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, _status_html().encode("utf-8"))
            return
        if self.path == "/settings":
            self._send(200, _settings_html().encode("utf-8"))
            return
        if self.path == "/api/status":
            cfg = load_config()
            body = json.dumps([s.to_dict() for s in check_all(cfg)],
                              indent=2, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if self.path == "/api/config":
            body = json.dumps(redact(load_config()), indent=2,
                              ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if self.path != "/settings":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        form = urllib.parse.parse_qs(raw, keep_blank_values=True)
        cfg = load_config()
        for pid, block in cfg["providers"].items():
            for field, value in list(block.items()):
                if field in ("id", "kind"):
                    continue
                key = f"{pid}__{field}"
                if key not in form:
                    continue
                values = form[key]
                if isinstance(value, bool):
                    # checkbox: hidden "false" sits before the box's "true"
                    block[field] = "true" in values
                else:
                    block[field] = values[-1]
        save_config(cfg)
        self._send(200, _settings_html("Saved.").encode("utf-8"))


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = http.server.HTTPServer((host, port), _Handler)
    print(f"ailimit web UI: http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        httpd.server_close()
