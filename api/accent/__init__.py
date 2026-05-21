"""Accent marking package.

Public API: the two FastAPI routers consumed by `main.py`. See
`README.md` for the pipeline overview and field semantics.
"""

from api.accent.routes import accent_router, furigana_router

__all__ = ["accent_router", "furigana_router"]
