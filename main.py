"""
An API interface that provide two functionalities
(1) Accent Marker   (/api/MarkAccent/)
(2) Furigana Marker (/api/MarkFurigana/)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import accent_marker, furigana_marker, usage_query, dict_query

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers from different modules
app.include_router(accent_marker.router, prefix="/api")
app.include_router(furigana_marker.router, prefix="/api")
app.include_router(usage_query.router, prefix='/api')
app.include_router(dict_query.router, prefix='/api')
    