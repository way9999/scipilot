"""Unpaywall OA PDF downloader.

Unpaywall indexes open-access PDF locations for 40M+ articles.
Free API, 100k requests/day, requires email parameter.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

_UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
_last_request_time = 0.0


def get_oa_url(doi: str, email: str = "scipilot@research.tool") -> str | None:
    """Query Unpaywall for the best OA PDF URL for a given DOI."""
    global _last_request_time
    if not doi:
        return None

    elapsed = time.time() - _last_request_time
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    try:
        resp = requests.get(
            f"{_UNPAYWALL_BASE}/{doi}",
            params={"email": email},
            timeout=15,
            headers={"User-Agent": "SciPilot/1.0"},
        )
        _last_request_time = time.time()
        if resp.status_code != 200:
            return None

        data = resp.json()

        # Check best OA location
        best = data.get("best_oa_location") or {}
        url = best.get("url_for_pdf") or best.get("url_for_landing_page")
        if url and url.lower().endswith(".pdf"):
            return url

        # Check all OA locations for a direct PDF
        for loc in data.get("oa_locations") or []:
            pdf_url = loc.get("url_for_pdf")
            if pdf_url:
                return pdf_url

        return None
    except Exception:
        return None


def download_pdf(
    doi: str,
    output_dir: str = "papers",
    email: str = "scipilot@research.tool",
) -> str | None:
    """Download OA PDF via Unpaywall. Returns local path or None."""
    pdf_url = get_oa_url(doi, email)
    if not pdf_url:
        return None

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    try:
        resp = requests.get(
            pdf_url,
            timeout=60,
            stream=True,
            headers={"User-Agent": "SciPilot/1.0"},
        )
        if resp.status_code != 200 or len(resp.content) < 1000:
            return None

        safe_doi = doi.replace("/", "_").replace("\\", "_")
        filename = f"unpaywall_{safe_doi}.pdf"
        filepath = Path(output_dir) / filename

        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        return str(filepath) if filepath.exists() and filepath.stat().st_size > 1000 else None
    except Exception:
        return None
