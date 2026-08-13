# va-lis-mcp

An MCP server for the **Virginia Legislative Information System (LIS) REST
API**. It exposes the Virginia General Assembly's sessions, bills, summaries,
full bill text, and action history to any MCP client (Claude Code, Claude
Desktop, and others). Every tool is read only. You bring your own LIS API key.

Built on [va-lis-client](https://pypi.org/project/va-lis-client/), a Python
client for the LIS API.

> **Status: not yet on PyPI.** The `uvx va-lis-mcp` snippets below are written
> for the first release. Until then, install from a source checkout (see
> [Installing from source](#installing-from-source)).

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/), which provides the `uvx` launcher used
  below. Install it with one line:
  `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or
  `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows)
- A LIS API key (next section)

## Getting an API key

Register for a free partner key at
<https://lis.virginia.gov/apiregistration>. The key is a GUID. The server
reads it from the `LIS_API_KEY` environment variable.

LIS heartbeat endpoints accept any key, even garbage, so a successful ping
proves nothing. Use the `check_api_key` tool to confirm the key is registered
and active.

## Install and configure

### Claude Code

```bash
claude mcp add va-lis -e LIS_API_KEY=your-guid-here -- uvx va-lis-mcp
```

### Claude Desktop and other MCP clients

Add this to the client's MCP configuration (for Claude Desktop that is
`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "va-lis": {
      "command": "uvx",
      "args": ["va-lis-mcp"],
      "env": { "LIS_API_KEY": "your-guid-here" }
    }
  }
}
```

### Installing from source

Until the first PyPI release, point the client at a checkout instead:

```bash
git clone https://github.com/JonnyWaffles/va-lis-mcp
claude mcp add va-lis -e LIS_API_KEY=your-guid-here -- uv run --directory /path/to/va-lis-mcp va-lis-mcp
```

The same works in a JSON config with `"command": "uv"` and
`"args": ["run", "--directory", "/path/to/va-lis-mcp", "va-lis-mcp"]`.

## Tools

| Tool | Inputs | Returns |
|---|---|---|
| `get_current_session` | none | The GA's working session, with a note on what "current" means during the interim |
| `list_sessions` | `year` | Sessions for a calendar year with their codes (reference data reaches back to 1994) |
| `list_bills` | `session_code`, `query?`, `limit?`, `offset?` | Bill rows filtered by a substring match on number, description, and title; paginated, with a total match count |
| `get_bill` | `bill_number`, `session_code` | Full bill detail: title, status, dates, patrons grouped by role, and the session lineage list |
| `get_bill_summaries` | `bill_number`, `session_code` | Plain text summaries (as introduced, as passed); the active entry is the current one |
| `get_bill_text` | `bill_number`, `session_code`, `document_code?` | Plain text of one version (default: most recent), truncated at 15,000 characters, plus the list of available versions |
| `get_bill_history` | `bill_number`, `session_code` | Chronological events with date, description, actor, committee, vote tally, status, and passage flags |
| `check_api_key` | none | Whether the configured key is registered and active |

Errors come back as readable tool errors. A bill lookup that misses names the
bill and session it searched; a missing key tells you to set `LIS_API_KEY`
and where to register.

## Domain notes

Distilled from the [va-lis-client
documentation](https://github.com/JonnyWaffles/va-lis-client), which is the
full reference for LIS behavior.

- **Session codes** encode year plus sequence: `20261` is the 2026 Regular
  Session, `20262` is 2026 Special Session I. Most tools require one.
- **Bill numbers are unpadded.** LIS returns nothing for `HB0001`; the
  correct form is `HB1`. The tools normalize input (`hb0001` becomes `HB1`),
  so you can pass either.
- **The default session tracks the GA's working session.** Once a session
  wraps up, the default advances to the upcoming session during the interim.
  By August 2026 it already pointed at `20271`. For retrospective work on a
  session that just ended, pass its explicit session code.
- **Carry over reuses bill IDs.** A bill from an even year session may be
  carried over into the following odd year session (never the reverse, at
  most once). The carried bill keeps its numeric `LegislationID` in both
  sessions, so a bill is identified by session code plus bill number. The
  `sessions` list on `get_bill` is the lineage record: two entries mean the
  bill was carried over.
- **Passage dates live in the history.** The bill detail's passage date
  fields are always null in LIS; `get_bill_history` flags passage events and
  carries the vote tallies.

## Embedding in an ASGI app

Besides stdio, the server can be served over streamable HTTP. The packaged
ASGI app is stateless and returns plain JSON, so it works behind multiple
workers without sticky sessions:

```bash
uvicorn va_lis_mcp.asgi:app --workers 4
```

The MCP endpoint is at `/mcp`. To mount it inside a larger Starlette (or any
ASGI) application, the host app's lifespan must run the MCP session manager:

```python
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount

from va_lis_mcp.server import mcp


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Mount("/lis", app=mcp.streamable_http_app(stateless_http=True, json_response=True)),
    ],
    lifespan=lifespan,
)
```

That serves the endpoint at `/lis/mcp`. Keep `stateless_http=True` whenever
more than one worker handles requests: the default stateful transport needs
every request of a session to reach the same worker, which a plain worker
pool does not guarantee.

This is ASGI only; a WSGI stack cannot host it. Django projects can still
serve it, but through their ASGI entry point (for example gunicorn with
uvicorn workers), not through WSGI.

## Development

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest
uv run ruff check .
```

Poke at the tools interactively with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run va-lis-mcp
```

## Upgrading

`uvx` caches resolved environments, so after a new release force resolution
of the latest version:

```bash
uvx va-lis-mcp@latest
```

## License

MIT
