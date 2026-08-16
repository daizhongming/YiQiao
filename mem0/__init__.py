# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import importlib.metadata

try:
    __version__ = importlib.metadata.version("yiqiao")
except importlib.metadata.PackageNotFoundError:
    __version__ = "1.0.0+source"

from mem0.memory.main import AsyncMemory, Memory

__all__ = ["AsyncMemory", "Memory"]
