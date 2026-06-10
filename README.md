# ailimit

Local quota / status monitor for three AI providers:

| Provider | What we read | How |
|---|---|---|
| **Codex** | live 5-hour & 7-day rate-limit window, plan type | chatgpt.com browser cookie → `/api/auth/session` → `/backend-api/codex/usage`. **Read-only**, never invokes `codex app-server`, so it cannot trigger a fresh 5-hour window. |
| **GLM** | auth check; quota only if you wire it up | OpenAI-compatible `GET {base_url}/models` with Bearer key. Quota is fetched **only** if you set `optional_quota_url` + `optional_quota_json_path`. |
| **MiniMax** | same as GLM | same as GLM |

**No fake numbers.** If GLM or MiniMax has no documented public quota endpoint and you haven't pointed `optional_quota_url` at one, the UI shows `quota: not_configured`. Token counts from local logs are **never** displayed as remaining quota.

## Install

```bash
pip install -r requirements.txt   # only browser-cookie3, for Codex
```

Python 3.8+ on macOS. `browser-cookie3` needs Keychain access to decrypt Chrome cookies.

## CLI

```bash
python3 usage.py                  # status (default)
python3 usage.py status
python3 usage.py config --show    # masked dump of ~/.ailimit/config.json
python3 usage.py config --set glm.enabled=true \
                       --set glm.api_key=sk-... \
                       --set glm.base_url=https://open.bigmodel.cn/api/paas/v4
python3 usage.py server --port 8765   # web UI at http://127.0.0.1:8765
```

`config --set` accepts `provider.field=value`. Booleans like `enabled` are coerced from `true/false/1/0/yes/no/on/off`.

## Web UI

`python3 usage.py server` launches a stdlib `http.server` (no Flask) on `127.0.0.1:8765`:

- `/` — status table for every provider (auth, quota, source, last_checked, error)
- `/settings` — form to edit each provider's fields, posts back to `/settings` and writes `~/.ailimit/config.json`
- `/api/status` — same data as `/`, as JSON
- `/api/config` — current config with `api_key` masked, as JSON

## Configuration

Stored at `~/.ailimit/config.json` (created on first save with `chmod 600`). A template lives at [config.example.json](./config.example.json) — copy and edit if you prefer hand-editing JSON.

Per provider:

| Field | Codex | GLM / MiniMax | Notes |
|---|---|---|---|
| `enabled` | ✓ | ✓ | turn the provider off without deleting its config |
| `display_name` | ✓ | ✓ | label in status output |
| `api_key` | — | ✓ | Bearer token; stored locally only |
| `base_url` | — | ✓ | OpenAI-compatible root (e.g. `https://open.bigmodel.cn/api/paas/v4`) |
| `optional_quota_url` | — | ✓ | full URL of a quota / balance JSON endpoint, if your vendor exposes one |
| `optional_quota_json_path` | — | ✓ | dotted path into that JSON to the number you want (e.g. `data.balance`, `usage.0.remaining`) |
| `use_app_server_fallback` | ✓ | — | **defaults to false** and we recommend leaving it false; flipping it on would let Codex spawn `codex app-server`, which can trigger a new 5-hour window |

## Why GLM / MiniMax quota is opt-in

Neither vendor publishes a stable, documented OpenAI-style quota endpoint. If you have an internal balance URL — or your vendor adds one — set `optional_quota_url` and `optional_quota_json_path` and we'll read it with your Bearer key. Otherwise the most we can honestly say is "API key works" (i.e. `/models` returned 200).

## Architecture

```
usage.py       CLI: status / config / server
app.py         minimal http.server web UI
providers.py   Provider abstract + CodexProvider + OpenAICompatProvider
settings.py    read/write ~/.ailimit/config.json
```

`providers.check_all()` returns a list of `ProviderStatus`. The CLI, the web UI, and the JSON API all render the same objects, so adding a menu bar layer later (e.g. `rumps`) only needs a new `app_menubar.py` that calls the same function.

## Limitations

- macOS only for Codex (Keychain-decrypted browser cookies).
- Codex `app-server` path is intentionally disabled.
- GLM and MiniMax quota numbers only appear if you supply a working `optional_quota_url`.
- API keys never enter git; `~/.ailimit/config.json` and `config.json` are gitignored.
