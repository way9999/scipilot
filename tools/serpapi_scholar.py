"""Google Scholar search via SerpAPI.

SerpAPI provides a reliable Google Scholar API with a free tier.
Requires SERPAPI_KEY environment variable.

Free tier: 100 searches/month
Pricing: https://serpapi.com/search-api
"""

from __future__ import annotations

import os
from typing import Any

import requests

_SERPAPI_BASE = "https://serpapi.com/search"


def _get_key() -> str | None:
    return os.environ.get("SERPAPI_KEY", "").strip()


def search_papers(
    query: str,
    limit: int = 10,
    year_low: int | None = None,
    year_high: int | None = None,
) -> list[dict[str, Any]]:
    """Search Google Scholar via SerpAPI.

    Returns empty list if SERPAPI_KEY is not set or request fails.
    """
    api_key = _get_key()
    if not api_key:
        return []

    params: dict[str, Any] = {
        "engine": "google_scholar",
        "q": query,
        "api_key": api_key,
        "num": min(limit, 20),
    }

    if year_low or year_high:
        yl = year_low or 1900
        yh = year_high or 2100
        params["as_ylo"] = yl
        params["as_yhi"] = yh

    try:
        resp = requests.get(_SERPAPI_BASE, params=params, timeout=30)
        if resp.status_code != 200:
            return []

        data = resp.json()
        results: list[dict[str, Any]] = []

        for item in data.get("organic_results", [])[:limit]:
            title = item.get("title", "")
            if not title:
                continue

            authors: list[str] = []
            pub_info = item.get("publication_info", {})
            summary = pub_info.get("summary", "")
            if summary:
                parts = summary.split("-")
                if parts:
                    authors = [a.strip() for a in parts[0].split(",") if a.strip()]

            year: int | None = None
            if summary:
                import re
                ym = re.search(r"(20\d{2}|19\d{2})", summary)
                if ym:
                    year = int(ym.group(1))

            link = item.get("link", "")
            result_id = item.get("result_id", "")

            results.append({
                "title": title,
                "authors": authors,
                "year": year,
                "doi": "",
                "url": link,
                "abstract": item.get("snippet", "")[:2000],
                "venue": pub_info.get("journal", ""),
                "citation_count": 0,
                "source": "google_scholar",
                "result_id": result_id,
            })

        return results
    except Exception:
        return []


def has_api_key() -> bool:
    """Check if SerpAPI key is configured."""
    return bool(_get_key())
