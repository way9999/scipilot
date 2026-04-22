from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from io import BytesIO
from pathlib import Path
import json
import re
from typing import Any

import requests

from tools.paper_dashboard import build_dashboard
from tools.project_state import load_paper_index, save_paper_index, sync_project_state

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment]


USER_AGENT = "SciWorkspacePaperCrawler/1.0"
REQUEST_TIMEOUT = (10, 45)
MIN_CONTENT_WORDS = 80
MAX_CONTENT_CHARS = 60000
MAX_PARAGRAPHS = 120


@dataclass
class CrawlAttempt:
    kind: str
    target: str
    success: bool
    detail: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._") or "paper"


def _normalize_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _word_count(text: str) -> int:
    if not text:
        return 0
    if re.search(r"[A-Za-z0-9]", text):
        return len(re.findall(r"\b[\w-]+\b", text))
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _excerpt(text: str, limit: int = 280) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _resolve_local_path(project_root: Path, raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (project_root / candidate).resolve()


def _clean_html_fragment(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _html_title(document: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", document, flags=re.IGNORECASE | re.DOTALL)
    return _clean_html_fragment(match.group(1)) if match else ""


def _extract_html_text(document: str) -> tuple[str, dict[str, Any]]:
    stripped = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", document, flags=re.IGNORECASE | re.DOTALL)
    paragraphs = [
        _clean_html_fragment(fragment)
        for fragment in re.findall(r"<p\b[^>]*>(.*?)</p>", stripped, flags=re.IGNORECASE | re.DOTALL)
    ]
    paragraphs = [paragraph for paragraph in paragraphs if len(paragraph) >= 30]

    if paragraphs:
        text = "\n\n".join(paragraphs[:MAX_PARAGRAPHS])
    else:
        body = re.search(r"<body[^>]*>(.*?)</body>", stripped, flags=re.IGNORECASE | re.DOTALL)
        text = _clean_html_fragment(body.group(1) if body else stripped)

    return (
        _normalize_text(text)[:MAX_CONTENT_CHARS],
        {
            "title": _html_title(document),
            "paragraph_count": min(len(paragraphs), MAX_PARAGRAPHS),
        },
    )


def _extract_pdf_text(reader: Any) -> tuple[str, dict[str, Any]]:
    pages_processed = 0
    page_texts: list[str] = []

    for page in reader.pages:
        if len("\n\n".join(page_texts)) >= MAX_CONTENT_CHARS:
            break
        extracted = (page.extract_text() or "").strip()
        if extracted:
            page_texts.append(extracted)
        pages_processed += 1

    return (
        _normalize_text("\n\n".join(page_texts))[:MAX_CONTENT_CHARS],
        {
            "page_count": len(reader.pages),
            "pages_processed": pages_processed,
        },
    )


def _crawl_local_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed; install tools requirements to extract local PDFs.")
    reader = PdfReader(str(path))
    return _extract_pdf_text(reader)


def _crawl_remote_pdf(url: str) -> tuple[str, dict[str, Any]]:
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed; install tools requirements to extract remote PDFs.")
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    reader = PdfReader(BytesIO(response.content))
    text, meta = _extract_pdf_text(reader)
    meta["fetched_url"] = response.url
    return text, meta


def _crawl_remote_html(url: str) -> tuple[str, dict[str, Any]]:
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    text, meta = _extract_html_text(response.text)
    meta["fetched_url"] = response.url
    meta["content_type"] = response.headers.get("content-type", "")
    return text, meta


def _write_output(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def _render_markdown(record: dict[str, Any], payload: dict[str, Any], markdown_path: Path, json_path: Path) -> str:
    lines = [
        f"# {record.get('title') or 'Untitled Paper'}",
        "",
        f"- Record ID: `{record.get('record_id', '')}`",
        f"- Source: {payload['source_type']}",
        f"- Crawled at: {payload['crawled_at']}",
        f"- Word count: {payload['word_count']}",
        f"- Source URL: {payload.get('resolved_url') or record.get('url') or record.get('pdf_url') or 'n/a'}",
        f"- Local file: {record.get('local_path') or 'n/a'}",
        f"- Markdown path: `{markdown_path}`",
        f"- JSON path: `{json_path}`",
        "",
    ]

    if payload.get("excerpt"):
        lines.extend(["## Excerpt", payload["excerpt"], ""])

    lines.extend(["## Extracted Content", payload["content"] or "No content extracted.", ""])
    return "\n".join(lines)


def crawl_paper_content(record_id: str, project_root: str | Path = ".") -> dict[str, Any]:
    root = Path(project_root).resolve()
    papers = load_paper_index(root)
    target = next((paper for paper in papers if paper.get("record_id") == record_id), None)
    if not target:
        raise ValueError(f"Paper with record_id '{record_id}' was not found in the paper index.")

    attempts: list[CrawlAttempt] = []
    content = ""
    source_type = ""
    source_meta: dict[str, Any] = {}
    resolved_url = ""

    local_path = _resolve_local_path(root, target.get("local_path"))
    if local_path and local_path.exists() and local_path.suffix.lower() == ".pdf":
        try:
            content, source_meta = _crawl_local_pdf(local_path)
            source_type = "local_pdf"
            attempts.append(CrawlAttempt("local_pdf", str(local_path), True, f"{_word_count(content)} words extracted"))
        except Exception as error:
            attempts.append(CrawlAttempt("local_pdf", str(local_path), False, str(error)))

    for candidate_url in [target.get("pdf_url"), target.get("url")]:
        if source_type or not candidate_url:
            continue

        try:
            if str(candidate_url).lower().endswith(".pdf"):
                content, source_meta = _crawl_remote_pdf(candidate_url)
                source_type = "remote_pdf"
            else:
                content, source_meta = _crawl_remote_html(candidate_url)
                source_type = "html_page"
            resolved_url = source_meta.get("fetched_url") or candidate_url
            attempts.append(CrawlAttempt(source_type, candidate_url, True, f"{_word_count(content)} words extracted"))
        except Exception as error:
            attempts.append(CrawlAttempt("remote", candidate_url, False, str(error)))

    content = _normalize_text(content)
    word_count = _word_count(content)
    if word_count < MIN_CONTENT_WORDS:
        details = "; ".join(f"{attempt.kind}:{attempt.detail}" for attempt in attempts) or "no crawl attempt was possible"
        raise RuntimeError(f"Unable to extract enough paper content ({word_count} words). Attempts: {details}")

    safe_record_id = _safe_slug(str(record_id))
    markdown_rel = Path("knowledge-base") / "papers" / f"{safe_record_id}.md"
    json_rel = Path("output") / "paper-content" / f"{safe_record_id}.json"
    markdown_path = root / markdown_rel
    json_path = root / json_rel

    payload = {
        "record_id": record_id,
        "title": target.get("title", ""),
        "source_type": source_type,
        "resolved_url": resolved_url,
        "crawled_at": _now_iso(),
        "word_count": word_count,
        "excerpt": _excerpt(content),
        "content": content,
        "source_meta": source_meta,
        "attempts": [attempt.__dict__ for attempt in attempts],
    }

    _write_output(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    _write_output(markdown_path, _render_markdown(target, payload, markdown_rel, json_rel))

    target["content_path"] = str(markdown_rel).replace("\\", "/")
    target["content_json_path"] = str(json_rel).replace("\\", "/")
    target["content_crawled"] = True
    target["content_source"] = source_type
    target["content_excerpt"] = payload["excerpt"]
    target["content_word_count"] = word_count
    target["content_updated_at"] = payload["crawled_at"]
    save_paper_index(papers, root)

    state = sync_project_state(root)
    dashboard_path = build_dashboard(root)
    return {
        "project_root": str(root),
        "dashboard_path": str(dashboard_path),
        "paper": target,
        "content_path": str(markdown_path),
        "content_json_path": str(json_path),
        "word_count": word_count,
        "source_type": source_type,
        "resolved_url": resolved_url,
        "state": state,
    }
