# ailimit

**macOS status-bar quota monitor for Codex, GLM, and MiniMax.** Sits in your menu bar as `C 84% [battery.100]  G 72% [battery.75]  M 91% [battery.100]` — short tag, 5h percent, and a system SF Symbol battery icon (`battery.0/25/50/75/100`, `setTemplate_(True)` so light/dark follow system). Refreshes every 5 minutes. Real numbers from the vendors' own endpoints — no fakes.

| Provider | What we read | How |
|---|---|---|
| **Codex** | live 5-hour & 7-day rate-limit window, plan type | chatgpt.com browser cookie → `/api/auth/session` → `/backend-api/codex/usage`. **Read-only**; never invokes `codex app-server`, so it cannot trigger a fresh 5-hour window. |
| **GLM** (Zhipu Coding Plan) | live 5h & weekly token quota | `GET https://api.z.ai/api/monitor/usage/quota/limit` with `Authorization: <api_key>` (no Bearer). |
| **MiniMax** (Token Plan) | live 5h & weekly remaining percent | `GET https://api.minimaxi.com/v1/token_plan/remains` (or `api.minimax.io` if base_url points there) with `Authorization: Bearer <api_key>`. |

**No fake numbers.** When `api_key` is empty or set to `***`, the provider is reported as `not_configured` and **no** HTTP request is made. Real failures surface as `auth: failed` / `quota: unavailable` with the underlying status code or message.

## Install (macOS, status bar)

```bash
./install.sh
```

That script:

1. Copies the app to `~/.ailimit/app/`
2. Creates a venv at `~/.ailimit/venv/` and installs requirements
3. Writes a LaunchAgent plist at `~/Library/LaunchAgents/com.ailimit.menubar.plist`
4. Loads the agent — the `AI …` item shows up in your menu bar

Logs land in `~/.ailimit/logs/menubar.{out,err}.log`.

## Uninstall

```bash
./uninstall.sh            # leaves app/, venv/, and config.json in place
./uninstall.sh --purge    # also removes app/ and venv/  (config.json preserved)
./uninstall.sh --purge-all  # removes app, venv, logs, and config.json
```

Your `~/.ailimit/config.json` (api keys) is **never** removed by default.

## Configure

Two ways:

- **Menu bar → Open Settings** — web form on `http://127.0.0.1:8765/settings`. The menubar app starts the web server in a background thread the first time you open it.
- **CLI**:
  ```bash
  python3 usage.py config --set glm.enabled=true --set glm.api_key=YOUR_GLM_KEY
  python3 usage.py config --set minimax.enabled=true --set minimax.api_key=YOUR_KEY
  python3 usage.py config --show    # masked dump of ~/.ailimit/config.json
  ```

Booleans (`enabled`) coerce from `true/false/1/0/yes/no/on/off`.

The web UI also serves a status table at `/` and a JSON snapshot at `/api/status`.

## Status bar legend

Each provider is rendered as `<tag> <percent>% [battery icon]`. Tags are `C` (Codex), `G` (GLM), `M` (MiniMax). The battery icon is an SF Symbol — `battery.0` / `battery.25` / `battery.50` / `battery.75` / `battery.100` — selected by the percent, with `setTemplate_(True)` so the system handles light/dark/vibrancy automatically.

| Mark | Meaning |
|---|---|
| `C 84% [battery.100]` | Codex live percent of 5h quota remaining, with battery icon matching the percent |
| `G -` | GLM disabled or not configured (no fake number, no battery) |
| `M ⚠` | MiniMax live call failed (HTTP error, bad key, etc.) — see dropdown for details |

If AppKit/PyObjC isn't importable (e.g. running off-macOS), the title falls back to a plain-text bar: `C 84% ▰▰▱▱  G -  M ⚠`.

The dropdown shows the full per-provider block: `auth`, `quota`, `source`, `last_checked`, `error`. Items: `Refresh Now` · `Open Settings` · `Open Web UI` · `Quit`.

## Configuration reference

Stored at `~/.ailimit/config.json` (created on first save with `chmod 600`). Template: [config.example.json](./config.example.json). Real keys never enter git.

| Field | Codex | GLM | MiniMax | Notes |
|---|---|---|---|---|
| `enabled` | ✓ | ✓ | ✓ | turn the provider off without deleting its config |
| `display_name` | ✓ | ✓ | ✓ | label in status output and web UI |
| `api_key` | — | ✓ | ✓ | stored locally only; use `***` (or empty) to disable the live call without removing the block |
| `base_url` | — | ✓ | ✓ | for MiniMax, set to `https://api.minimax.io` to route to the global endpoint; otherwise the domestic `api.minimaxi.com` is used |
| `use_app_server_fallback` | ✓ | — | — | **defaults to false** and we recommend leaving it false; flipping it on would let Codex spawn `codex app-server`, which can trigger a new 5-hour window |

### Legacy `MiniMax` block

Configs created by earlier versions used the id `MiniMax`. On load, those blocks are migrated into `minimax` automatically; next save persists the canonical layout. You don't need to do anything.

## Architecture

```
usage.py       CLI: status / config / server
app.py         stdlib http.server web UI
menubar.py     macOS status-bar app (rumps) — same check_all() contract
providers.py   Provider implementations + factory:
                 CodexProvider   (chatgpt.com cookie, read-only)
                 GLMProvider     (z.ai TOKENS_LIMIT)
                 MiniMaxProvider (token_plan/remains)
                 OpenAICompatProvider (kept for future custom providers)
settings.py    read/write ~/.ailimit/config.json (chmod 600)
install.sh     copies app to ~/.ailimit/app, builds venv, registers LaunchAgent
uninstall.sh   unloads agent, removes plist (keeps config by default)
```

`providers.check_all()` is the single source of truth — the CLI, the web UI, the JSON API, and the menu bar all render the same `ProviderStatus` objects.

## Quota details

### GLM (Zhipu Coding Plan)

1. `GET https://api.z.ai/api/monitor/usage/quota/limit`
2. Header `Authorization: <api_key>` — **no Bearer prefix**.
3. Success = `HTTP 200` AND JSON `success: true`.
4. From `data.limits[]`, filter `type == "TOKENS_LIMIT"`, sort by `nextResetTime` ascending. First entry's `percentage` = 5h used, second = weekly used. Remaining = `100 - percentage`, clamped to 0–100.
5. Errors: HTTP 401 / 429, or `success: false` (e.g. `Unauthorized`), surface verbatim.

### MiniMax (Token Plan)

1. `GET {base_url}/v1/token_plan/remains` — `api.minimax.io` if `base_url` contains `minimax.io`, otherwise `api.minimaxi.com`.
2. Header `Authorization: Bearer <api_key>`.
3. HTTP 200 alone is **not** success — `base_resp.status_code == 0` is required. `1004` = invalid token, `1005` = no permission, `1024` = quota exhausted.
4. From `model_remains[]`, skip `video / audio / image / music / speech`. Prefer `model_name == "general"`; otherwise the first remaining entry.
5. Used % = `100 - current_interval_remaining_percent` (and `current_weekly_remaining_percent`). If the percent field is missing or 0, fall back to `round(100 * current_interval_usage_count / current_interval_total_count)` for 5h.
6. `remains_time` and `weekly_remains_time` (seconds) are shown alongside as `(in 2h15m)`.

## Limitations

- macOS only (rumps / PyObjC for the status bar; Keychain for Codex cookies).
- Codex `app-server` path is intentionally disabled.
- The installer accepts Python 3.11-3.13. It prefers `python3.13` → `python3.12` → `python3.11`; if none of those exist, it falls back to `python3` only when that binary reports a 3.11-3.13 version. Python 3.14 is not yet supported because `browser-cookie3` has no 3.14 wheel; if found, the installer prints a clear "needs Python 3.11-3.13 because browser-cookie3 may not support 3.14 yet" error and exits.
- API keys never enter git; `~/.ailimit/config.json` and `config.json` are gitignored.
- Both GLM and MiniMax quota endpoints are unofficial vendor endpoints and may change without notice.
