"""ASGI entry point for serving the MCP server over streamable HTTP.

Run with::

    uvicorn va_lis_mcp.asgi:app

The MCP endpoint is served at ``/mcp``. The app is stateless with plain JSON
responses, so it works behind multiple workers without sticky sessions::

    uvicorn va_lis_mcp.asgi:app --workers 4

To mount inside a larger ASGI application instead, see the README: the host
app's lifespan must run ``mcp.session_manager.run()``.
"""

from va_lis_mcp.server import mcp

app = mcp.streamable_http_app(stateless_http=True, json_response=True)
