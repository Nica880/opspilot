# MCP and A2A

OpsPilot exposes the same four capabilities to other agents over two protocols. Both go through
`agent/tools.py`, which has no write path to GitHub (enforced by a test that inspects its imports).

| Capability | MCP tool | A2A skill | Effect |
|---|---|---|---|
| Hybrid search over issues, PRs, discussions, docs | `search_issues` | `search_issues` | read-only |
| Issue thread with comments (redacted, marked untrusted) | `get_thread` | (use MCP) | read-only |
| Type / area / severity / labels from the repo's vocabulary | `suggest_labels` | `suggest_labels` | read-only |
| Start the durable triage workflow | `triage_issue` | `triage_issue` | proposes actions; a human approves in the UI |

Repos are limited to `REPOS`, queries to 2,000 characters, `k` to 20. Input errors come back as
tool errors. Unexpected exceptions are hidden by the MCP SDK, so stack traces never leak.

## MCP

- Streamable HTTP (stateless, JSON responses): `http://localhost:8000/mcp/`
- stdio: `uv run python -m api.mcp_server` (or `make mcp-stdio`)

Claude Desktop (`claude_desktop_config.json`), with the compose stack running:

```json
{
  "mcpServers": {
    "opspilot": {
      "command": "uv",
      "args": ["--directory", "/path/to/opspilot", "run", "python", "-m", "api.mcp_server"],
      "env": {
        "DATABASE_URL": "postgresql://opspilot:opspilot@localhost:5432/opspilot",
        "QDRANT_URL": "http://localhost:6333",
        "TEMPORAL_ADDRESS": "localhost:7233"
      }
    }
  }
}
```

## A2A (v1.0)

- Agent card: `GET http://localhost:8000/.well-known/agent-card.json`
- JSON-RPC binding: `POST http://localhost:8000/a2a/v1` with header `A2A-Version: 1.0`
- Methods: `SendMessage`, `GetTask`

```bash
curl -s localhost:8000/a2a/v1 -H 'A2A-Version: 1.0' -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
  "params": {"message": {"role": "ROLE_USER", "messageId": "m1",
    "parts": [{"data": {"skill": "suggest_labels", "repo": "astral-sh/uv",
                        "title": "uv lock fails behind a proxy"}}]}}}'
```

Read-only skills answer with a `message` whose part carries the JSON result. `triage_issue`
answers with a `task`. It stays `TASK_STATE_INPUT_REQUIRED` until a human decides in the
approval queue, and then becomes `COMPLETED` or `REJECTED` (poll with `GetTask`).
