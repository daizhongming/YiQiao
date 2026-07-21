# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Filesystem paths used by the YiQiao Python package."""

import os


def resolve_yiqiao_dir(home_dir: str | None = None) -> str:
    """Return the YiQiao state directory without hiding existing legacy state."""

    configured = os.environ.get("YIQIAO_DIR") or os.environ.get("MEM0_DIR")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    resolved_home = os.path.expanduser("~") if home_dir is None else os.path.expanduser(home_dir)
    canonical = os.path.join(resolved_home, ".yiqiao")
    legacy = os.path.join(resolved_home, ".mem0")
    if not os.path.exists(canonical) and os.path.exists(legacy):
        return legacy
    return canonical
