# va-lis-mcp

An MCP server for the **Virginia Legislative Information System (LIS) REST
API**. It exposes the Virginia General Assembly's sessions, bills, summaries,
full bill text, action history, votes and roll calls, members, committees,
and meetings to any MCP client (Claude Code, Claude Desktop, and others).
Every tool is read only. You bring your own LIS API key.

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

Every tool returns compact JSON. Bills are addressed by bill number plus
session code, members by `member_id`, and committees by number (`H08`) or
name.

### Sessions and bills

| Tool | Inputs | Returns |
|---|---|---|
| `get_current_session` | none | The GA's working session, with a note on what "current" means during the interim |
| `list_sessions` | `year` | Sessions for a calendar year with their codes (reference data reaches back to 1994) |
| `list_bills` | `session_code`, `query?`, `limit?`, `offset?` | Bill rows filtered by a substring match on number, description, and title; paginated, with a total match count |
| `get_bill` | `bill_number`, `session_code` | Full bill detail: title, status, dates, patrons grouped by role, and the session lineage list |
| `get_bill_patrons` | `bill_number`, `session_code` | Every patron with member id, role, and chamber, in LIS display order |
| `get_bill_summaries` | `bill_number`, `session_code` | Plain text summaries (as introduced, as passed); the active entry is the current one |
| `get_bill_text` | `bill_number`, `session_code`, `document_code?` | Plain text of one version (default: most recent), truncated at 15,000 characters, plus the list of available versions |
| `get_bill_history` | `bill_number`, `session_code` | Chronological events with date, description, actor, committee, vote tally, `vote_id`, status, and passage flags |

### Votes

| Tool | Inputs | Returns |
|---|---|---|
| `get_bill_votes` | `bill_number`, `session_code` | Every vote the bill saw in either chamber (floor, committee, subcommittee) with tally, outcome, and the voice, block, and roll call flags |
| `get_roll_call` | `bill_number`, `session_code`, `vote_id?` | Members grouped as yea, nay, abstain, and not voting, each with party and district, plus any vote statements; voice and block votes are left out |
| `get_member_votes` | `member_id`, `session_code`, `bill_number?`, `exclude_block_votes?`, `limit?`, `offset?` | One member's votes across a session or on one bill, with block votes flagged; paginated |

### Members

| Tool | Inputs | Returns |
|---|---|---|
| `list_members` | `session_code`, `query?`, `chamber?` | The session roster, searchable by any part of a name, departed members included |
| `get_member` | `member_id`, `session_code` | One roster record with party, district, status, and contact details |
| `get_member_bills` | `member_id`, `session_code`, `role?`, `query?`, `limit?`, `offset?` | Every bill the member patrons, with chief patronage flagged; paginated |
| `get_member_committees` | `member_id`, `session_code`, `include_subcommittees?` | The member's committee seats and roles |

### Committees

| Tool | Inputs | Returns |
|---|---|---|
| `list_committees` | `chamber?`, `include_subcommittees?` | The standing committees, with subcommittees on request |
| `get_committee` | `committee`, `session_code`, `chamber?` | The committee's roster for the session with each member's role, party, and district, its regular meeting note, and its subcommittees |

### Meetings

| Tool | Inputs | Returns |
|---|---|---|
| `list_meetings` | `start_date`, `end_date`, `committee?`, `chamber?`, `meeting_type?`, `limit?`, `offset?` | The master schedule for a date range: date, the clerk's time text, room, owner, and cancellation flag; paginated |
| `list_dockets` | `committee`, `session_code` | A Senate committee's dockets with the bills on each (the House keeps none) |
| `list_floor_calendars` | `chamber`, `session_code` | A chamber's floor calendars for the session |
| `get_floor_calendar` | `calendar_id` | The bills on one floor calendar by section, with the actions and vote ids recorded against each |

### Diagnostics

| Tool | Inputs | Returns |
|---|---|---|
| `check_api_key` | none | Whether the configured key is registered and active |

Errors come back as readable tool errors. A bill lookup that misses names the
bill and session it searched; an ambiguous committee name lists the matches;
an unknown member id points at `list_members`; a missing key tells you to set
`LIS_API_KEY` and where to register.

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
- **Votes reach a bill through its history.** No LIS vote endpoint accepts a
  bill id, so `get_bill_votes` walks the bill's events and fetches the vote
  behind each one. `get_bill_history` carries the `vote_id` on each event.
- **A block vote is not a position on one bill.** A chamber bundles its
  uncontroversial bills and passes them in one motion; one 2026 House vote
  disposed of 105 bills at 97 to 0. A voice vote records no members at all.
  `get_roll_call` leaves both out, `get_bill_votes` flags them, and
  `get_member_votes` marks each row with `is_block`.
- **Not voting is not in the tally.** Members recorded as `not_voting` (LIS
  code `X`) are omitted from the tally string, so the member groups never add
  up to it. Vote statements are corrections a member filed afterwards; the
  recorded roll call is never amended.
- **The roster is larger than the chamber, on purpose.** A session roster
  includes members who left or arrived mid-session, because they cast the
  votes recorded before they left. `list_members` keeps them and marks the
  departure date and reason.
- **Committees are not session scoped; their seats are.** The committee
  list describes the committees that exist now, and `get_committee` reads the
  seat list for the session you name. LIS has no "bills referred to a
  committee" endpoint; `get_bill_history` names the committee that holds a
  bill.
- **Dockets are Senate only.** House committee agendas are not in the API:
  `list_meetings` gives the meeting, the bill's history gives the referral,
  and the agenda itself lives on the committee's web page. Schedule times are
  free text as the clerk entered them and may be blank.

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
