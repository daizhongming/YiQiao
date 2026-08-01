# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import importlib.metadata

try:
    __version__ = importlib.metadata.version("yiqiao")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.2.2+source"

from mem0.memory.main import AsyncMemory, Memory

__all__ = ["AsyncMemory", "Memory"]
