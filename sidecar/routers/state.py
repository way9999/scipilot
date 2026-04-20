"""State, refresh, and recommendations endpoints."""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, HTTPException

from tools.project_state import (
    detect_project_state,
    load_paper_index,
    sync_project_state,
)
from tools.paper_dashboard import build_dashboard, build_dashboard_payload

router = APIRouter(tags=["state"])


def _project_root() -> Path:
    env_root = os.environ.get("SCIPILOT_USER_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _project_root()

# Coverage thresholds from CLAUDE.md
COVERAGE_THRESHOLDS = {
    "background": 3,
    "method": 2,
    "problem": 2,
}


@router.get("/state")
async def get_state():
    try:
        state = await asyncio.to_thread(detect_project_state, PROJECT_ROOT)
        return {"success": True, "data": state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh")
async def refresh():
    try:
        state = await asyncio.to_thread(sync_project_state, PROJECT_ROOT)
        dashboard_path = await asyncio.to_thread(build_dashboard, PROJECT_ROOT)
        papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)
        return {
            "success": True,
            "data": {
                "paper_count": len(papers),
                "state": state,
                "dashboard_path": str(dashboard_path),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/papers")
async def get_papers(discipline: str | None = None, source: str | None = None):
    try:
        papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)

        if discipline:
            papers = [p for p in papers if p.get("discipline") == discipline]
        if source:
            papers = [p for p in papers if p.get("source") == source]

        return {"success": True, "data": papers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard")
async def get_dashboard():
    try:
        payload = await asyncio.to_thread(build_dashboard_payload, PROJECT_ROOT)
        return {"success": True, "data": payload}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _build_recommendations(papers: list[dict], state: dict) -> list[dict]:
    """Analyze papers and state to generate actionable recommendations."""
    recs: list[dict] = []
    current_stage = state.get("current_stage", "focus")
    artifacts = state.get("artifacts", {})

    total = len(papers)
    verified = sum(1 for p in papers if p.get("verified"))
    downloaded = sum(1 for p in papers if p.get("downloaded") or p.get("local_path"))
    unverified = total - verified
    not_downloaded = total - downloaded

    # Stage-based recommendations
    if current_stage == "focus":
        recs.append({
            "type": "action",
            "priority": "high",
            "title": "Start literature search",
            "description": "Define your research question and search for papers to build your knowledge base.",
            "action": "search",
        })

    if current_stage == "literature" and total < 5:
        recs.append({
            "type": "warning",
            "priority": "high",
            "title": "Low paper count",
            "description": f"Only {total} papers indexed. Aim for at least 10 papers for a solid literature base.",
            "action": "search",
        })

    # Coverage analysis
    if total > 0:
        # Check verification rate
        if unverified > 0 and unverified / total > 0.5:
            recs.append({
                "type": "warning",
                "priority": "medium",
                "title": f"{unverified} unverified papers",
                "description": f"Over half your papers are unverified. Batch-verify to ensure citation accuracy.",
                "action": "batch-verify",
                "count": unverified,
            })

        # Check download rate
        if not_downloaded > 3:
            recs.append({
                "type": "info",
                "priority": "medium",
                "title": f"{not_downloaded} papers not downloaded",
                "description": "Download PDFs for full-text access during writing.",
                "action": "batch-download",
                "count": not_downloaded,
            })

        # Source diversity
        sources = Counter(p.get("source", "unknown") for p in papers)
        if len(sources) == 1:
            recs.append({
                "type": "info",
                "priority": "low",
                "title": "Single source",
                "description": f"All papers from {list(sources.keys())[0]}. Try different disciplines or sources for broader coverage.",
                "action": "search",
            })

        # Suggest related queries from paper titles
        if total >= 3:
            keywords = _extract_keywords(papers)
            if keywords:
                recs.append({
                    "type": "suggestion",
                    "priority": "low",
                    "title": "Related search queries",
                    "description": f"Based on your papers, try: {', '.join(keywords[:5])}",
                    "action": "search",
                    "queries": keywords[:5],
                })

    # Stage transition recommendations
    if current_stage == "literature" and total >= 7 and verified >= 5:
        recs.append({
            "type": "action",
            "priority": "high",
            "title": "Ready for structure phase",
            "description": "You have enough verified literature. Consider freezing your outline.",
            "action": "advance",
        })

    if current_stage == "structure" and not state.get("outline_frozen"):
        recs.append({
            "type": "action",
            "priority": "high",
            "title": "Freeze outline",
            "description": "Review and freeze your outline to begin writing.",
            "action": "freeze",
        })

    if current_stage == "writing":
        draft_count = artifacts.get("draft_files", 0)
        if draft_count == 0:
            recs.append({
                "type": "action",
                "priority": "high",
                "title": "Start writing",
                "description": "Your outline is frozen. Begin drafting chapters.",
                "action": "write",
            })

    return sorted(recs, key=lambda r: {"high": 0, "medium": 1, "low": 2}.get(r.get("priority", "low"), 3))


def _extract_keywords(papers: list[dict]) -> list[str]:
    """Extract common meaningful terms from paper titles for query suggestions."""
    import re

    stopwords = {
        "a", "an", "the", "of", "in", "for", "and", "or", "to", "on", "with",
        "is", "are", "was", "were", "by", "from", "at", "as", "its", "this",
        "that", "be", "has", "have", "been", "not", "but", "can", "do", "does",
        "using", "based", "via", "towards", "toward", "through", "between",
        "new", "novel", "approach", "method", "study", "analysis", "paper",
    }

    word_counts: Counter[str] = Counter()
    for paper in papers:
        title = paper.get("title", "")
        words = re.findall(r"[a-zA-Z]{3,}", title.lower())
        for word in words:
            if word not in stopwords:
                word_counts[word] += 1

    # Return words that appear in multiple papers
    return [word for word, count in word_counts.most_common(10) if count >= 2]


@router.get("/recommendations")
async def get_recommendations():
    """Generate smart recommendations based on current project state and papers."""
    try:
        state = await asyncio.to_thread(detect_project_state, PROJECT_ROOT)
        papers = await asyncio.to_thread(load_paper_index, PROJECT_ROOT)
        recs = _build_recommendations(papers, state)
        return {
            "success": True,
            "data": {
                "recommendations": recs,
                "paper_count": len(papers),
                "current_stage": state.get("current_stage", "focus"),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
