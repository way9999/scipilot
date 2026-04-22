"""Search, download, and content extraction endpoints."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tools.paper_content_crawler import crawl_paper_content
from tools.paper_dashboard import build_dashboard
from tools.project_models import normalize_paper_dict
from tools.project_state import (
    load_paper_index,
    register_search_results,
    save_paper_index,
    sync_project_state,
)
from tools.unified_search import auto_download, auto_search, verify_paper

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


class CrawlRequest(BaseModel):
    record_id: str


class VerifyRequest(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None


class BatchDownloadRequest(BaseModel):
    record_ids: list[str]


class BatchVerifyRequest(BaseModel):
    record_ids: list[str]


class BatchCrawlRequest(BaseModel):
    record_ids: list[str]


def _normalize_results(papers: list[dict[str, Any]], discipline: str) -> list[dict[str, Any]]:
    return [
        normalize_paper_dict(
            paper,
            source=paper.get("source") or paper.get("_source"),
            discipline=discipline,
        )
        for paper in papers
    ]


def _find_paper(papers: list[dict[str, Any]], record_id: str) -> dict[str, Any] | None:
    return next((paper for paper in papers if paper.get("record_id") == record_id), None)


def _merge_search_results(
    results: list[dict[str, Any]], papers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    indexed = {
        str(paper.get("record_id")): paper
        for paper in papers
        if paper.get("record_id")
    }
    merged: list[dict[str, Any]] = []
    for result in results:
        record_id = str(result.get("record_id") or "")
        merged.append(dict(indexed.get(record_id) or result))
    return merged


async def _refresh_state() -> dict[str, Any]:
    state = await asyncio.to_thread(sync_project_state, PROJECT_ROOT)
    await asyncio.to_thread(build_dashboard, PROJECT_ROOT)
    return state


async def _try_crawl(record_id: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        result = await asyncio.to_thread(crawl_paper_content, record_id, PROJECT_ROOT)
        return result, None
    except Exception as error:
        return None, str(error)


@router.post("/search")
async def search_papers(req: SearchRequest):
    try:
        raw_results = await asyncio.to_thread(
            auto_search, req.query, discipline=req.discipline, limit=req.limit
        )
        results = await asyncio.to_thread(
            _normalize_results, raw_results, req.discipline
        )
        record_ids = [
            str(result["record_id"])
            for result in results
            if result.get("record_id")
        ]

        merged = await asyncio.to_thread(
            register_search_results,
            results,
            project_root=PROJECT_ROOT,
            discipline=req.discipline,
            query=req.query,
        )

        downloaded_count = 0
        crawled_count = 0
        crawl_failures: list[dict[str, str]] = []

        if req.download and record_ids:
            papers_dir = PROJECT_ROOT / "papers"
            papers_dir.mkdir(parents=True, exist_ok=True)

            changed = False
            for record_id in record_ids:
                target = _find_paper(merged, record_id)
                if not target:
                    continue
                local_path = await asyncio.to_thread(
                    auto_download, target, output_dir=str(papers_dir)
                )
                if not local_path:
                    continue
                target["local_path"] = str(local_path)
                target["downloaded"] = True
                downloaded_count += 1
                changed = True

            if changed:
                await asyncio.to_thread(save_paper_index, merged, PROJECT_ROOT)

            for record_id in record_ids:
                target = _find_paper(merged, record_id)
                if not target or target.get("content_crawled"):
                    continue
                if not (
                    target.get("local_path")
                    or target.get("pdf_url")
                    or target.get("url")
                ):
                    continue
                crawl_result, crawl_error = await _try_crawl(record_id)
                if crawl_result:
                    updated = crawl_result.get("paper") or {}
                    target.update(updated)
                    crawled_count += 1
                elif crawl_error:
                    crawl_failures.append(
                        {"record_id": record_id, "error": crawl_error}
                    )

            if crawled_count:
                merged = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)

        state = await _refresh_state()
        return {
            "success": True,
            "data": {
                "query": req.query,
                "discipline": req.discipline,
                "result_count": len(results),
                "downloaded_count": downloaded_count,
                "crawled_count": crawled_count,
                "crawl_failed": len(crawl_failures),
                "crawl_failures": crawl_failures,
                "indexed_count": len(merged),
                "results": _merge_search_results(results, merged),
                "state": state,
            },
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/download")
async def download_paper(req: DownloadRequest):
    try:
        papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)
        target = _find_paper(papers, req.record_id)
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

        crawl_result = None
        crawl_error = None
        if not target.get("content_crawled") and (
            target.get("local_path") or target.get("pdf_url") or target.get("url")
        ):
            crawl_result, crawl_error = await _try_crawl(req.record_id)

        if crawl_result:
            return {
                "success": True,
                "data": {
                    "paper": crawl_result.get("paper"),
                    "state": crawl_result.get("state"),
                    "crawled": True,
                    "crawl_error": None,
                },
            }

        state = await _refresh_state()
        return {
            "success": True,
            "data": {
                "paper": target,
                "state": state,
                "crawled": bool(target.get("content_crawled")),
                "crawl_error": crawl_error,
            },
        }
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/crawl")
async def crawl_paper(req: CrawlRequest):
    papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)
    target = _find_paper(papers, req.record_id)
    if not target:
        raise HTTPException(
            status_code=404,
            detail=f"Paper with record_id '{req.record_id}' not found.",
        )

    try:
        result = await asyncio.to_thread(crawl_paper_content, req.record_id, PROJECT_ROOT)
        return {"success": True, "data": result}
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/verify")
async def verify_paper_endpoint(req: VerifyRequest):
    try:
        result = await asyncio.to_thread(
            verify_paper, req.title, authors=req.authors
        )
        return {"success": True, "data": result}
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/batch-download")
async def batch_download(req: BatchDownloadRequest):
    papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)
    papers_dir = PROJECT_ROOT / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)

    results = []
    downloadable_ids: list[str] = []
    changed = False

    for record_id in req.record_ids:
        target = _find_paper(papers, record_id)
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
                downloadable_ids.append(record_id)
                results.append({"record_id": record_id, "success": True, "downloaded": True})
                changed = True
            else:
                results.append({"record_id": record_id, "success": False, "error": "Download failed"})
        except Exception as error:
            results.append({"record_id": record_id, "success": False, "error": str(error)})

    if changed:
        await asyncio.to_thread(save_paper_index, papers, PROJECT_ROOT)

    for item in results:
        if not item.get("success"):
            item["crawled"] = False
            continue

        record_id = item["record_id"]
        target = _find_paper(papers, record_id)
        if not target or target.get("content_crawled"):
            item["crawled"] = bool(target and target.get("content_crawled"))
            continue
        if not (
            target.get("local_path") or target.get("pdf_url") or target.get("url")
        ):
            item["crawled"] = False
            item["crawl_error"] = "No local or remote content source available"
            continue

        crawl_result, crawl_error = await _try_crawl(record_id)
        if crawl_result:
            updated = crawl_result.get("paper") or {}
            target.update(updated)
            item["crawled"] = True
        else:
            item["crawled"] = False
            item["crawl_error"] = crawl_error

    state = await _refresh_state()
    succeeded = sum(1 for item in results if item["success"])
    crawled = sum(1 for item in results if item.get("crawled"))
    return {
        "success": True,
        "data": {
            "total": len(req.record_ids),
            "succeeded": succeeded,
            "failed": len(req.record_ids) - succeeded,
            "crawled": crawled,
            "results": results,
            "state": state,
        },
    }


@router.post("/batch-verify")
async def batch_verify(req: BatchVerifyRequest):
    papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)

    results = []
    for record_id in req.record_ids:
        target = _find_paper(papers, record_id)
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
        except Exception as error:
            results.append({"record_id": record_id, "success": False, "error": str(error)})

    await asyncio.to_thread(save_paper_index, papers, PROJECT_ROOT)

    verified_count = sum(1 for item in results if item.get("verified"))
    return {
        "success": True,
        "data": {
            "total": len(req.record_ids),
            "verified": verified_count,
            "results": results,
        },
    }


@router.post("/batch-crawl")
async def batch_crawl(req: BatchCrawlRequest):
    papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)

    results = []
    for record_id in req.record_ids:
        target = _find_paper(papers, record_id)
        if not target:
            results.append({"record_id": record_id, "success": False, "error": "Not found"})
            continue
        if target.get("content_crawled"):
            results.append({"record_id": record_id, "success": True, "crawled": False, "skipped": True})
            continue
        if not (
            target.get("local_path") or target.get("pdf_url") or target.get("url")
        ):
            results.append({
                "record_id": record_id,
                "success": False,
                "error": "No local or remote content source available",
            })
            continue

        crawl_result, crawl_error = await _try_crawl(record_id)
        if crawl_result:
            results.append({"record_id": record_id, "success": True, "crawled": True})
        else:
            results.append({"record_id": record_id, "success": False, "error": crawl_error or "Crawl failed"})

    state = await _refresh_state()
    succeeded = sum(1 for item in results if item["success"])
    crawled = sum(1 for item in results if item.get("crawled"))
    return {
        "success": True,
        "data": {
            "total": len(req.record_ids),
            "succeeded": succeeded,
            "failed": len(req.record_ids) - succeeded,
            "crawled": crawled,
            "results": results,
            "state": state,
        },
    }
