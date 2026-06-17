from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.chat import router as chat_router
from api.compress import router as compress_router
from api.rag_api import router as rag_router
from api.sessions import router as sessions_router
from api.tokens import router as tokens_router
from api.web_content import router as web_content_router
from config import get_settings
from graph.agent import agent_manager
from tools.skills_scanner import refresh_snapshot

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    refresh_snapshot(settings.backend_dir)
    agent_manager.initialize(settings.backend_dir)

    yield


app = FastAPI(
    title="transparent agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(tokens_router, prefix="/api", tags=["tokens"])
app.include_router(compress_router, prefix="/api", tags=["compress"])
app.include_router(rag_router, prefix="/api/config", tags=["config"])
app.include_router(web_content_router, prefix="/api", tags=["web-content"])


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "请求处理失败。"
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "http_error", "message": detail}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_server_error",
                "message": "服务器内部错误，请稍后重试或查看服务日志。",
            }
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
