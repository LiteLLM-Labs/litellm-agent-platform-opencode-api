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
OPENCODE_INFERENCE_API_KEY=sk-ant-... python3 -m opencode_api.server
```

If your shell has a broken global Node install, put a working Node/OpenCode path
first:

```bash
PATH="$HOME/.nvm/versions/node/v20.19.5/bin:$PATH" \
OPENCODE_INFERENCE_API_KEY=sk-ant-... \
python3 -m opencode_api.server
```

Point OpenCode at an Anthropic-compatible gateway:

```bash
OPENCODE_INFERENCE_BASE_URL=https://litellm-rust.onrender.com \
OPENCODE_INFERENCE_API_KEY=sk-... \
python3 -m opencode_api.server
```

If the base URL does not end in `/v1`, the service appends `/v1` before passing
it to OpenCode as `ANTHROPIC_BASE_URL`.

Require bearer auth for all runtime routes except `/health`:

```bash
OPENCODE_API_KEY=sk-provider OPENCODE_INFERENCE_API_KEY=sk-ant-... python3 -m opencode_api.server
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

## Render

This repo includes a Dockerfile for Render. Set these environment variables on
the Render service:

- `OPENCODE_API_KEY`: bearer token LAP uses to call this provider
- `OPENCODE_INFERENCE_BASE_URL`: Anthropic-compatible gateway base URL, for
  example `https://litellm-rust.onrender.com`
- `OPENCODE_INFERENCE_API_KEY`: key sent by OpenCode to that gateway
