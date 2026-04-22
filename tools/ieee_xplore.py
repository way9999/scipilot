"""IEEE Xplore Metadata API search backend.

Uses the official IEEE Xplore Metadata API via direct HTTP requests.
Requires IEEE_API_KEY environment variable.

Rate limits (free tier): 10 calls/sec, 200 calls/day.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import requests

_IEEE_BASE = "http://ieeexploreapi.ieee.org/api/v1/search"
_last_request_time = 0.0


def _get_key() -> str | None:
    return os.environ.get("IEEE_API_KEY", "").strip()


def search_papers(
    query: str,
    limit: int = 10,
    year: str | None = None,
) -> list[dict[str, Any]]:
    """Search IEEE Xplore Metadata API.

    Returns empty list if IEEE_API_KEY is not set or request fails.
    """
    api_key = _get_key()
    if not api_key:
        return []

    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 0.2:
        time.sleep(0.2 - elapsed)

    params: dict[str, Any] = {
        "querytext": query,
        "apikey": api_key,
        "max_records": min(limit, 200),
        "start_record": 1,
        "output_format": "json",
        "sort_order": "desc",
        "sort_field": "relevance",
    }

    if year:
        if "-" in year:
            parts = year.split("-")
            params["start_year"] = parts[0].strip()
            if parts[1].strip():
                params["end_year"] = parts[1].strip()
        else:
            params["start_year"] = year
            params["end_year"] = year

    try:
        resp = requests.get(_IEEE_BASE, params=params, timeout=20,
                            headers={"User-Agent": "SciPilot/1.0"})
        _last_request_time = time.time()
        if resp.status_code != 200:
            return []

        data = resp.json()
        results: list[dict[str, Any]] = []

        for item in data.get("results", [])[:limit]:
            title = (item.get("title") or "").strip()
            if not title:
                continue

            authors: list[str] = []
            for a in (item.get("authors", {}).get("authors") or []):
                name = a.get("full_name", "").strip()
                if name:
                    authors.append(name)

            year: int | None = None
            pub_year = item.get("publication_year")
            if pub_year:
                try:
                    year = int(pub_year)
                except (ValueError, TypeError):
                    pass

            doi = (item.get("doi") or "").strip()
            pdf_url = (item.get("pdf_url") or "").strip()
            url = (item.get("html_url") or doi or "").strip()

            abstract = (item.get("abstract") or "").strip()[:2000]

            venue = (item.get("publication_title") or "").strip()

            citation_count = 0
            try:
                citation_count = int(item.get("citing_paper_count") or 0)
            except (ValueError, TypeError):
                pass

            ieee_id = (item.get("article_number") or "").strip()

            results.append({
                "title": title,
                "authors": authors,
                "year": year,
                "doi": doi,
                "url": url,
                "pdf_url": pdf_url,
                "abstract": abstract,
                "venue": venue,
                "citation_count": citation_count,
                "source": "ieee_xplore",
                "ieee_id": ieee_id,
                "content_type": item.get("content_type", ""),
                "publisher": "IEEE",
            })

        return results
    except Exception:
        return []


def search_by_doi(doi: str) -> dict[str, Any] | None:
    """Look up a single paper by DOI on IEEE Xplore."""
    api_key = _get_key()
    if not api_key:
        return None

    params = {
        "doi": doi,
        "apikey": api_key,
        "max_records": 1,
        "output_format": "json",
    }

    try:
        resp = requests.get(_IEEE_BASE, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        items = data.get("results", [])
        if not items:
            return None

        item = items[0]
        title = (item.get("title") or "").strip()
        if not title:
            return None

        authors = [a["full_name"].strip()
                    for a in (item.get("authors", {}).get("authors") or [])
                    if a.get("full_name")]

        year = None
        try:
            year = int(item.get("publication_year") or 0) or None
        except (ValueError, TypeError):
            pass

        return {
            "title": title,
            "authors": authors,
            "year": year,
            "doi": (item.get("doi") or "").strip(),
            "url": (item.get("html_url") or "").strip(),
            "pdf_url": (item.get("pdf_url") or "").strip(),
            "abstract": (item.get("abstract") or "").strip()[:2000],
            "venue": (item.get("publication_title") or "").strip(),
            "citation_count": int(item.get("citing_paper_count") or 0),
            "source": "ieee_xplore",
            "ieee_id": (item.get("article_number") or "").strip(),
        }
    except Exception:
        return None


def has_api_key() -> bool:
    return bool(_get_key())
