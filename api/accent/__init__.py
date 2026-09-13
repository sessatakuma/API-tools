"""Accent marking package.

Public API: the `accent_router` FastAPI router consumed by `main.py`.
See `README.md` for the pipeline overview and field semantics.
"""

from api.accent.routes import accent_router

__all__ = ["accent_router"]
