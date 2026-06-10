#!/usr/bin/env python3
"""ailimit CLI entry.

Subcommands:
  status                 (default) probe every provider and print a table
  config --show          dump merged config with api_key masked
  config --set p.field=value [--set ...]   patch one or more provider fields
  server [--port N]      run the web settings/status UI on 127.0.0.1
"""
from __future__ import annotations

import argparse
import json
import sys

from providers import ProviderStatus, check_all
from settings import CONFIG_PATH, load_config, redact, update_provider

import app


_BOLD = "\033[1m" if sys.stdout.isatty() else ""
_DIM = "\033[2m" if sys.stdout.isatty() else ""
_OK = "\033[32m" if sys.stdout.isatty() else ""
_WRN = "\033[33m" if sys.stdout.isatty() else ""
_CRT = "\033[31m" if sys.stdout.isatty() else ""
_RST = "\033[0m" if sys.stdout.isatty() else ""


def _color_for(state: str) -> str:
    if state == "ok":
        return _OK
    if state in ("disabled", "not_configured"):
        return _DIM
    if state == "unavailable":
        return _WRN
    if state == "failed":
        return _CRT
    return ""


def _fmt_status(s: ProviderStatus) -> str:
    lines = [f"{_BOLD}{s.display_name}{_RST}  {_DIM}({s.id}){_RST}"
             + ("" if s.enabled else f"  {_DIM}[disabled]{_RST}")]
    lines.append(
        f"  auth:  {_color_for(s.auth_status)}{s.auth_status}{_RST}"
        + (f"  {_DIM}{s.auth_detail}{_RST}" if s.auth_detail else "")
    )
    lines.append(
        f"  quota: {_color_for(s.quota_status)}{s.quota_status}{_RST}"
        + (f"  {_DIM}{s.quota_detail}{_RST}" if s.quota_detail else "")
    )
    if s.quota_source:
        lines.append(f"  source: {_DIM}{s.quota_source}{_RST}")
    if s.last_checked:
        lines.append(f"  checked: {_DIM}{s.last_checked}{_RST}")
    if s.error:
        lines.append(f"  error: {_CRT}{s.error}{_RST}")
    return "\n".join(lines)


def cmd_status(_args) -> int:
    cfg = load_config()
    statuses = check_all(cfg)
    for s in statuses:
        print(_fmt_status(s))
        print()
    return 0


def cmd_config(args) -> int:
    if args.show:
        print(json.dumps(redact(load_config()), indent=2, ensure_ascii=False))
        print(f"\n{_DIM}config file: {CONFIG_PATH}{_RST}")
        return 0
    if args.set:
        # Group sets by provider id, then apply once.
        per_provider: dict[str, dict] = {}
        for token in args.set:
            if "=" not in token or "." not in token.split("=", 1)[0]:
                print(f"invalid --set {token!r}; expected provider.field=value", file=sys.stderr)
                return 2
            lhs, value = token.split("=", 1)
            pid, field = lhs.split(".", 1)
            per_provider.setdefault(pid, {})[field] = value
        for pid, fields in per_provider.items():
            try:
                update_provider(pid, fields)
            except KeyError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            print(f"updated {pid}: {', '.join(fields)}")
        return 0
    print("nothing to do; pass --show or --set provider.field=value",
          file=sys.stderr)
    return 2


def cmd_server(args) -> int:
    app.serve(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ailimit",
                                description="local quota monitor for Codex / GLM / MiniMax")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status", help="probe every provider (default)")

    pc = sub.add_parser("config", help="show or patch local config")
    pc.add_argument("--show", action="store_true", help="print merged config (api_key masked)")
    pc.add_argument("--set", action="append", metavar="provider.field=value",
                    help="patch one field; repeat for multiple")

    ps = sub.add_parser("server", help="run the web settings/status UI")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8765)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.cmd or "status"
    return {"status": cmd_status, "config": cmd_config, "server": cmd_server}[cmd](args)


if __name__ == "__main__":
    sys.exit(main())
