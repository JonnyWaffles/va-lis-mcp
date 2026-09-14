"""MCP server for the Virginia Legislative Information System (LIS) API."""

from importlib.metadata import version

# pyproject.toml holds the number. Reading it back keeps the two in step.
__version__ = version("va-lis-mcp")
