# ailimit

Local quota / status monitor for three AI providers.

| Provider | What we read | How |
|---|---|---|
| **Codex** | live 5-hour & 7-day rate-limit window, plan type | chatgpt.com browser cookie → `/api/auth/session` → `/backend-api/codex/usage`. **Read-only**; never invokes `codex app-server`, so it cannot trigger a fresh 5-hour window. |
| **GLM** (Zhipu Coding Plan) | live 5h & weekly token quota | `GET https://api.z.ai/api/monitor/usage/quota/limit` with `Authorization: <api_key>` (no Bearer). |
| **MiniMax** (Token Plan) | live 5h & weekly remaining percent | `GET https://api.minimaxi.com/v1/token_plan/remains` (or `api.minimax.io` if base_url points there) with `Authorization: Bearer <api_key>`. |

**No fake numbers.** When `api_key` is empty or set to `***`, the provider is reported as `not_configured` and **no** HTTP request is made. Real failures surface as `auth: failed` / `quota: unavailable` with the underlying status code or message.

## Install

```bash
pip install -r requirements.txt   # only browser-cookie3, for Codex
```

Python 3.8+ on macOS. `browser-cookie3` needs Keychain access to decrypt Chrome cookies. GLM and MiniMax paths are pure stdlib (`urllib`), no extra wheels required.

## CLI

```bash
python3 usage.py                  # status (default)
python3 usage.py status
python3 usage.py config --show    # masked dump of ~/.ailimit/config.json
python3 usage.py config --set glm.enabled=true --set glm.api_key=YOUR_GLM_KEY
python3 usage.py config --set minimax.enabled=true --set minimax.api_key=YOUR_KEY
python3 usage.py server --port 8765   # web UI at http://127.0.0.1:8765
```

`config --set` accepts `provider.field=value`. Booleans (`enabled`) coerce from `true/false/1/0/yes/no/on/off`.

## Web UI

`python3 usage.py server` launches a stdlib `http.server` (no Flask) on `127.0.0.1:8765`:

- `/` — status table for every provider (auth, quota, source, last_checked, error)
- `/settings` — form to edit each provider's `enabled / display_name / api_key / base_url`, posts back to `/settings` and writes `~/.ailimit/config.json`
- `/api/status` — same data as `/`, as JSON
- `/api/config` — current config with `api_key` masked, as JSON

## Configuration

Stored at `~/.ailimit/config.json` (created on first save with `chmod 600`). Template: [config.example.json](./config.example.json). Real keys never enter git.

Per provider:

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
app.py         minimal http.server web UI
providers.py   Provider implementations + factory:
                 CodexProvider  (chatgpt.com cookie, read-only)
                 GLMProvider    (z.ai TOKENS_LIMIT)
                 MiniMaxProvider (token_plan/remains)
                 OpenAICompatProvider (kept for future custom providers)
settings.py    read/write ~/.ailimit/config.json (chmod 600)
```

`providers.check_all()` returns a list of `ProviderStatus`. The CLI, the web UI, and the JSON API all render the same objects, so adding a menu bar layer (`rumps`) later only requires a new file that calls the same function.

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

- macOS only for Codex (Keychain-decrypted browser cookies).
- Codex `app-server` path is intentionally disabled.
- API keys never enter git; `~/.ailimit/config.json` and `config.json` are gitignored.
- Both GLM and MiniMax quota endpoints are unofficial vendor endpoints and may change without notice.
