from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from graph.agent import agent_manager
from utils.web_cache_db import WebCacheDB

router = APIRouter()


@router.get("/web-content")
async def get_web_content(url: str = Query(..., min_length=1), session_id: str = Query(..., min_length=1)):
    if agent_manager.base_dir is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")

    db_path = str(agent_manager.base_dir / "chroma_db" / "sessions" / session_id / "web_cache.db")
    cache_db = WebCacheDB(db_path)
    row = cache_db.get_by_url(url)

    if row is None:
        raise HTTPException(status_code=404, detail="URL not found in cache")

    return {"url": row["url"], "title": row["title"] or "", "content": row["content"]}
