"""Landscape analysis endpoints — wraps tools/landscape_analysis.py."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tools.landscape_analysis import analyze_landscape, generate_landscape_report

router = APIRouter(prefix="/landscape", tags=["landscape"])


def _project_root() -> Path:
    env_root = os.environ.get("SCIPILOT_USER_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _project_root()


class LandscapeRequest(BaseModel):
    topic: str
    discipline: str = "generic"
    limit: int = Field(default=20, ge=1, le=50)
    year: str | None = None
    save: bool = True


@router.get("/health")
async def landscape_health():
    return {"status": "ok", "message": "Landscape analysis endpoints are live."}


@router.post("/analyze")
async def analyze(req: LandscapeRequest):
    """Run landscape analysis: search → extract → summarize."""
    try:
        result = await asyncio.to_thread(
            analyze_landscape,
            req.topic,
            discipline=req.discipline,
            limit=req.limit,
            year=req.year,
            save=req.save,
            project_root=PROJECT_ROOT,
        )
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/report")
async def report(req: LandscapeRequest):
    """Run full landscape analysis and generate a Markdown report file."""
    try:
        report_path = await asyncio.to_thread(
            generate_landscape_report,
            req.topic,
            discipline=req.discipline,
            limit=req.limit,
            year=req.year,
            save=req.save,
            project_root=PROJECT_ROOT,
        )
        return {
            "success": True,
            "data": {"report_path": str(report_path)},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
