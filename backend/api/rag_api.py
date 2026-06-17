from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from config import runtime_config

router = APIRouter()


class RagModeResponse(BaseModel):
    enabled: bool


class SetRagModeRequest(BaseModel):
    enabled: bool


@router.get("/rag-mode", response_model=RagModeResponse)
async def get_rag_mode():
    return RagModeResponse(enabled=runtime_config.get_rag_mode())


@router.put("/rag-mode", response_model=RagModeResponse)
async def set_rag_mode(payload: SetRagModeRequest):
    runtime_config.set_rag_mode(payload.enabled)
    return RagModeResponse(enabled=runtime_config.get_rag_mode())
