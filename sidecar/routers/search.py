"""Search & download endpoints — wraps tools/unified_search.py."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tools.unified_search import auto_download, auto_search, verify_paper
from tools.project_state import register_search_results, save_paper_index, load_paper_index, sync_project_state
from tools.paper_dashboard import build_dashboard

router = APIRouter(tags=["search"])


def _project_root() -> Path:
    env_root = os.environ.get("SCIPILOT_USER_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _project_root()


class SearchRequest(BaseModel):
    query: str
    discipline: str = "generic"
    limit: int = Field(default=10, ge=1, le=50)
    download: bool = False


class DownloadRequest(BaseModel):
    record_id: str


class VerifyRequest(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None


class BatchDownloadRequest(BaseModel):
    record_ids: list[str]


class BatchVerifyRequest(BaseModel):
    record_ids: list[str]


def _json_safe(obj: Any) -> Any:
    """Make Path objects JSON-serializable."""
    if isinstance(obj, Path):
        return str(obj)
    return obj


@router.post("/search")
async def search_papers(req: SearchRequest):
    try:
        results = await asyncio.to_thread(
            auto_search, req.query, discipline=req.discipline, limit=req.limit
        )

        downloaded_count = 0
        if req.download:
            papers_dir = PROJECT_ROOT / "papers"
            papers_dir.mkdir(parents=True, exist_ok=True)
            for paper in results:
                local_path = await asyncio.to_thread(
                    auto_download, paper, output_dir=str(papers_dir)
                )
                if local_path:
                    paper["local_path"] = str(local_path)
                    paper["downloaded"] = True
                    downloaded_count += 1

        merged = await asyncio.to_thread(
            register_search_results,
            results,
            project_root=PROJECT_ROOT,
            discipline=req.discipline,
            query=req.query,
        )
        state = await asyncio.to_thread(sync_project_state, PROJECT_ROOT)
        await asyncio.to_thread(build_dashboard, PROJECT_ROOT)

        return {
            "success": True,
            "data": {
                "query": req.query,
                "discipline": req.discipline,
                "result_count": len(results),
                "downloaded_count": downloaded_count,
                "indexed_count": len(merged),
                "results": results,
                "state": state,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/download")
async def download_paper(req: DownloadRequest):
    try:
        papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)
        target = next(
            (p for p in papers if p.get("record_id") == req.record_id), None
        )
        if not target:
            raise HTTPException(
                status_code=404,
                detail=f"Paper with record_id '{req.record_id}' not found.",
            )

        papers_dir = PROJECT_ROOT / "papers"
        papers_dir.mkdir(parents=True, exist_ok=True)
        local_path = await asyncio.to_thread(
            auto_download, target, output_dir=str(papers_dir)
        )
        if not local_path:
            raise HTTPException(status_code=502, detail="Unable to download PDF.")

        target["local_path"] = str(local_path)
        target["downloaded"] = True
        await asyncio.to_thread(save_paper_index, papers, PROJECT_ROOT)
        state = await asyncio.to_thread(sync_project_state, PROJECT_ROOT)
        await asyncio.to_thread(build_dashboard, PROJECT_ROOT)

        return {
            "success": True,
            "data": {"paper": target, "state": state},
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify")
async def verify_paper_endpoint(req: VerifyRequest):
    try:
        result = await asyncio.to_thread(
            verify_paper, req.title, authors=req.authors
        )
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch-download")
async def batch_download(req: BatchDownloadRequest):
    """Download multiple papers by record_id. Returns per-paper results."""
    papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)
    papers_dir = PROJECT_ROOT / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for record_id in req.record_ids:
        target = next((p for p in papers if p.get("record_id") == record_id), None)
        if not target:
            results.append({"record_id": record_id, "success": False, "error": "Not found"})
            continue

        try:
            local_path = await asyncio.to_thread(
                auto_download, target, output_dir=str(papers_dir)
            )
            if local_path:
                target["local_path"] = str(local_path)
                target["downloaded"] = True
                results.append({"record_id": record_id, "success": True})
            else:
                results.append({"record_id": record_id, "success": False, "error": "Download failed"})
        except Exception as e:
            results.append({"record_id": record_id, "success": False, "error": str(e)})

    await asyncio.to_thread(save_paper_index, papers, PROJECT_ROOT)
    await asyncio.to_thread(sync_project_state, PROJECT_ROOT)
    await asyncio.to_thread(build_dashboard, PROJECT_ROOT)

    succeeded = sum(1 for r in results if r["success"])
    return {
        "success": True,
        "data": {
            "total": len(req.record_ids),
            "succeeded": succeeded,
            "failed": len(req.record_ids) - succeeded,
            "results": results,
        },
    }


@router.post("/batch-verify")
async def batch_verify(req: BatchVerifyRequest):
    """Verify multiple papers by record_id."""
    papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)

    results = []
    for record_id in req.record_ids:
        target = next((p for p in papers if p.get("record_id") == record_id), None)
        if not target or not target.get("title"):
            results.append({"record_id": record_id, "success": False, "error": "Not found or no title"})
            continue

        try:
            verification = await asyncio.to_thread(
                verify_paper, target["title"], authors=target.get("authors", [])
            )
            target["verified"] = bool(verification.get("verified", False))
            target["verified_by"] = verification.get("verified_by", "")
            target["verification_score"] = verification.get("score")
            results.append({"record_id": record_id, "success": True, "verified": target["verified"]})
        except Exception as e:
            results.append({"record_id": record_id, "success": False, "error": str(e)})

    await asyncio.to_thread(save_paper_index, papers, PROJECT_ROOT)

    verified_count = sum(1 for r in results if r.get("verified"))
    return {
        "success": True,
        "data": {
            "total": len(req.record_ids),
            "verified": verified_count,
            "results": results,
        },
    }
