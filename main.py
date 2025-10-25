"""
An API interface that provide two functionalities
(1) Accent Marker   (/api/MarkAccent/)
(2) Furigana Marker (/api/MarkFurigana/)
(3) Usage Query    (/api/UsageQuery/)
(4) Dictionary Query (/api/DictQuery/)
(5) Sentence Query  (/api/SentenceQuery/)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import accent_marker, dict_query, furigana_marker, sentence_query, usage_query

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
app.include_router(usage_query.router, prefix="/api")
app.include_router(dict_query.router, prefix="/api")
app.include_router(sentence_query.router, prefix="/api")
