"""YiQiao MCP companion."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("yiqiao-mcp")
except PackageNotFoundError:
    __version__ = "0.2.2"

__all__ = ["__version__"]
