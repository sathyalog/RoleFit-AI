"""Owner vs public mode.

Owner mode (the author's own machine) reads/writes data/*.json and eval/*.json
and enables local-repo features. Public mode (e.g. Streamlit Community Cloud)
keeps every visitor's data in their own session only and never writes to disk.

APP_MODE="owner" | "public" forces a mode; otherwise owner mode is inferred
from the local projects directory existing.
"""
import os

from mcp_codebase import DEFAULT_PROJECTS_DIR


def is_public_mode() -> bool:
    mode = os.getenv("APP_MODE", "").strip().lower()
    if mode in ("owner", "public"):
        return mode == "public"
    return not os.path.exists(DEFAULT_PROJECTS_DIR)
