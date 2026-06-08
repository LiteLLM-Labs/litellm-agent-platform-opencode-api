# LiteLLM Agent Platform OpenCode API

OpenCode-compatible runtime provider for LiteLLM Agent Platform.

LAP already knows how to talk to an OpenCode server:

- `POST /session`
- `POST /session/{session_id}/message`
- `GET /event`

This service exposes those same routes. On `POST /session`, it starts an
`opencode serve` process, creates a real OpenCode session, stores the session
mapping, and returns the OpenCode session response. LAP can configure this
service as the OpenCode runtime `api_base`.

## Run

```bash
ANTHROPIC_API_KEY=sk-ant-... python3 -m opencode_api.server
```

If your shell has a broken global Node install, put a working Node/OpenCode path
first:

```bash
PATH="$HOME/.nvm/versions/node/v20.19.5/bin:$PATH" \
ANTHROPIC_API_KEY=sk-ant-... \
python3 -m opencode_api.server
```

Require bearer auth for all runtime routes except `/health`:

```bash
OPENCODE_API_KEY=sk-provider ANTHROPIC_API_KEY=sk-ant-... python3 -m opencode_api.server
```

## Configure LAP

Set the OpenCode runtime/provider base URL to this service:

```text
api_base = http://127.0.0.1:8088
api_key = sk-provider
```

The existing LAP OpenCode provider will invoke:

```text
POST http://127.0.0.1:8088/session
POST http://127.0.0.1:8088/session/{session_id}/message
GET  http://127.0.0.1:8088/event
```

## Local Smoke Test

```bash
python3 -m opencode_api.server
curl http://127.0.0.1:8088/health
curl -sS -X POST http://127.0.0.1:8088/session \
  -H 'content-type: application/json' \
  -d '{"title":"test"}'
```

## Production Note

Today this starts local `opencode serve` processes. The next production step is
to replace `SessionManager.start_opencode` with sandbox creation while keeping
the public routes unchanged for LAP compatibility.
