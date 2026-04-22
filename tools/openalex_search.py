"""OpenAlex academic search backend.

OpenAlex indexes 470M+ scholarly works with rich metadata.
Free API, rate-limited without key, high quota with free key.
"""

from __future__ import annotations

import os
from typing import Any

import pyalex
from pyalex import Works

_FIELDS = [
    "id", "doi", "title", "authorships", "publication_year",
    "primary_location", "type", "cited_by_count", "abstract_inverted_index",
    "topics", "open_access",
]


def _get_inverted_abstract(index: dict | None) -> str:
    if not index:
        return ""
    word_positions: list[tuple[str, int]] = []
    for word, positions in index.items():
        for pos in positions:
            word_positions.append((word, pos))
    word_positions.sort(key=lambda x: x[1])
    return " ".join(w for w, _ in word_positions)


def _parse_work(work: dict[str, Any]) -> dict[str, Any]:
    authors = []
    for a in work.get("authorships") or []:
        author = a.get("author", {})
        name = author.get("display_name", "")
        if name:
            authors.append(name)

    primary = work.get("primary_location") or {}
    source = (primary.get("source") or {}).get("display_name", "")
    pdf_url = (work.get("open_access") or {}).get("oa_url", "")
    landing_url = work.get("id", "")  # OpenAlex landing page
    doi = work.get("doi", "") or ""
    if doi and not doi.startswith("https://doi.org/"):
        doi = f"https://doi.org/{doi}"

    oa_status = (work.get("open_access") or {}).get("oa_status", "")
    abstract = _get_inverted_abstract(work.get("abstract_inverted_index"))

    topics = []
    for t in (work.get("topics") or [])[:3]:
        topics.append(t.get("display_name", ""))

    return {
        "title": (work.get("title") or "").strip(),
        "authors": authors,
        "year": work.get("publication_year"),
        "doi": doi.replace("https://doi.org/", "") if doi else "",
        "url": landing_url or doi,
        "pdf_url": pdf_url or "",
        "abstract": abstract[:2000] if abstract else "",
        "venue": source,
        "citation_count": work.get("cited_by_count", 0),
        "source": "openalex",
        "oa_status": oa_status,
        "topics": topics,
        "type": work.get("type", ""),
    }


def search_papers(
    query: str,
    limit: int = 10,
    year: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[dict[str, Any]]:
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        pyalex.config.api_key = api_key

    w = Works().search(query)

    if year:
        w = w.filter(publication_year=int(year))
    elif year_from or year_to:
        filt: dict[str, Any] = {}
        if year_from:
            filt["from_publication_year"] = year_from
        if year_to:
            filt["to_publication_year"] = year_to
        if filt:
            w = w.filter(**filt)

    results = w.get(per_page=min(limit, 50))
    return [_parse_work(r) for r in results if r.get("title")]


def search_by_doi(doi: str) -> dict[str, Any] | None:
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        pyalex.config.api_key = api_key

    if doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")

    results = Works().filter(doi=doi).get(per_page=1)
    if results:
        return _parse_work(results[0])
    return None
