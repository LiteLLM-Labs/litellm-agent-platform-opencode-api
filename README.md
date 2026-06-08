# LiteLLM Agent Platform OpenCode API

Small HTTP provider that gives LiteLLM Agent Platform a URL it can call to create
an OpenCode-backed runtime session.

The service owns the OpenCode process. LAP can treat this as a provider URL:

```text
LAP /session -> this API /sessions -> opencode serve -> OpenCode /session
```

## Run locally

```bash
python3 -m opencode_api.server
```

Require bearer auth for `POST /sessions`:

```bash
OPENCODE_API_KEY=sk-provider python3 -m opencode_api.server
```

Smoke-test without requiring OpenCode:

```bash
OPENCODE_MOCK=1 python3 -m opencode_api.server
curl http://127.0.0.1:8088/health
curl -sS -X POST http://127.0.0.1:8088/sessions \
  -H 'content-type: application/json' \
  -d '{"title":"test"}'
```

## API

### `GET /health`

Returns service health.

### `POST /sessions`

Creates an OpenCode server process and an OpenCode session.

Request:

```json
{
  "title": "ops-agent session",
  "workspace": "/tmp/lap-opencode/example",
  "port": 4096
}
```

Response:

```json
{
  "id": "lapoc_...",
  "sandbox_id": "lapoc_...",
  "opencode_base_url": "http://127.0.0.1:4096",
  "opencode_session_id": "ses_...",
  "auth": {
    "type": "basic",
    "username": "opencode",
    "password": "..."
  }
}
```

In production, run one API instance per host/worker pool and put the real sandbox
creation behind `SessionManager.start_opencode`. The LAP repo should only need
this service URL and API key.
