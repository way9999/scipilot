from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Mapping


SOURCE_PRIORITY = {
    "crossref": 100,
    "semantic_scholar": 90,
    "arxiv": 80,
    "pubmed": 75,
    "biorxiv": 70,
    "medrxiv": 70,
    "chemrxiv": 70,
    "paperscraper": 65,
    "google_scholar": 60,
    "manual": 50,
}


def normalize_source_name(source: str | None) -> str:
    if not source:
        return ""

    normalized = str(source).strip().lower()
    aliases = {
        "scholarly": "google_scholar",
    }
    return aliases.get(normalized, normalized)


def normalize_title(title: str | None) -> str:
    if not title:
        return ""

    lowered = str(title).strip().lower()
    lowered = re.sub(r"[^0-9a-z]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def parse_year(value: Any) -> int | None:
    if value in (None, ""):
        return None

    try:
        text = str(value).strip()
        match = re.search(r"(19|20)\d{2}", text)
        if match:
            return int(match.group(0))
        return int(text)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_identifier(value: Any) -> str:
    return _clean_text(value).strip("/")


def _ensure_authors(value: Any) -> list[str]:
    if value in (None, ""):
        return []

    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).split(" and ")

    authors = []
    for item in items:
        author = _clean_text(item)
        if author:
            authors.append(author)
    return authors


def _value_present(value: Any) -> bool:
    return value not in (None, "", [], {}, ())


def build_record_id(title: str, year: Any = None, doi: str | None = None, arxiv_id: str | None = None) -> str:
    clean_doi = _clean_identifier(doi).lower()
    if clean_doi:
        return f"doi:{clean_doi}"

    clean_arxiv = _clean_identifier(arxiv_id).lower()
    if clean_arxiv:
        return f"arxiv:{clean_arxiv}"

    normalized_title = normalize_title(title)
    normalized_year = parse_year(year)
    return f"title:{normalized_title}:{normalized_year or 'na'}"


@dataclass
class PaperRecord:
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    s2_id: str = ""
    url: str = ""
    pdf_url: str = ""
    local_path: str = ""
    content_path: str = ""
    content_json_path: str = ""
    source: str = ""
    sources: list[str] = field(default_factory=list)
    discipline: str = "generic"
    citation_count: int | None = None
    verified: bool = False
    verified_by: str = ""
    verification_score: int | None = None
    downloaded: bool = False
    content_crawled: bool = False
    content_source: str = ""
    content_excerpt: str = ""
    content_word_count: int | None = None
    content_updated_at: str = ""
    source_rank: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    record_id: str = ""

    def __post_init__(self):
        self.title = _clean_text(self.title)
        self.authors = _ensure_authors(self.authors)
        self.year = parse_year(self.year)
        self.abstract = _clean_text(self.abstract)
        self.venue = _clean_text(self.venue)
        self.doi = _clean_identifier(self.doi)
        self.arxiv_id = _clean_identifier(self.arxiv_id)
        self.s2_id = _clean_identifier(self.s2_id)
        self.url = _clean_text(self.url)
        self.pdf_url = _clean_text(self.pdf_url)
        self.local_path = _clean_text(self.local_path)
        self.content_path = _clean_text(self.content_path)
        self.content_json_path = _clean_text(self.content_json_path)
        self.source = normalize_source_name(self.source)
        self.discipline = _clean_text(self.discipline) or "generic"
        self.verified_by = normalize_source_name(self.verified_by)
        self.downloaded = bool(self.downloaded or self.local_path)
        self.content_crawled = bool(self.content_crawled or self.content_path)
        self.content_source = _clean_text(self.content_source)
        self.content_excerpt = _clean_text(self.content_excerpt)
        self.content_updated_at = _clean_text(self.content_updated_at)

        if self.citation_count in (None, ""):
            self.citation_count = None
        else:
            self.citation_count = int(self.citation_count)

        if self.content_word_count in (None, ""):
            self.content_word_count = None
        else:
            self.content_word_count = int(self.content_word_count)

        source_set = {normalize_source_name(source) for source in self.sources if source}
        if self.source:
            source_set.add(self.source)
        self.sources = sorted(source_set)

        self.source_rank = max(
            [self.source_rank, SOURCE_PRIORITY.get(self.source, 0)]
            + [SOURCE_PRIORITY.get(source, 0) for source in self.sources]
        )

        if not self.record_id:
            self.record_id = build_record_id(self.title, self.year, self.doi, self.arxiv_id)

    @classmethod
    def from_raw(
        cls,
        raw: Mapping[str, Any],
        source: str | None = None,
        discipline: str = "generic",
    ) -> "PaperRecord":
        resolved_source = normalize_source_name(source or raw.get("_source") or raw.get("source"))
        known_keys = {
            "_source",
            "abstract",
            "arxiv_id",
            "authors",
            "categories",
            "citation_count",
            "comment",
            "discipline",
            "doi",
            "downloaded",
            "journal",
            "journal_ref",
            "local_path",
            "content_path",
            "content_json_path",
            "content_crawled",
            "content_source",
            "content_excerpt",
            "content_word_count",
            "content_updated_at",
            "pdf_url",
            "primary_category",
            "record_id",
            "s2_id",
            "score",
            "source",
            "sources",
            "title",
            "type",
            "url",
            "venue",
            "verified",
            "verified_by",
            "verification_score",
            "year",
        }

        metadata = {
            key: value
            for key, value in raw.items()
            if key not in known_keys and _value_present(value)
        }

        return cls(
            title=raw.get("title", ""),
            authors=raw.get("authors", []),
            year=raw.get("year"),
            abstract=raw.get("abstract", ""),
            venue=raw.get("venue") or raw.get("journal") or raw.get("journal_ref", ""),
            doi=raw.get("doi", ""),
            arxiv_id=raw.get("arxiv_id", ""),
            s2_id=raw.get("s2_id", ""),
            url=raw.get("url", ""),
            pdf_url=raw.get("pdf_url", ""),
            local_path=raw.get("local_path", ""),
            content_path=raw.get("content_path", ""),
            content_json_path=raw.get("content_json_path", ""),
            source=resolved_source,
            sources=list(raw.get("sources", [])),
            discipline=discipline or raw.get("discipline", "generic"),
            citation_count=raw.get("citation_count"),
            verified=bool(raw.get("verified", False)),
            verified_by=raw.get("verified_by", ""),
            verification_score=raw.get("verification_score") or raw.get("score"),
            downloaded=bool(raw.get("downloaded", False)),
            content_crawled=bool(raw.get("content_crawled", False)),
            content_source=raw.get("content_source", ""),
            content_excerpt=raw.get("content_excerpt", ""),
            content_word_count=raw.get("content_word_count"),
            content_updated_at=raw.get("content_updated_at", ""),
            metadata=metadata,
            record_id=raw.get("record_id", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["_source"] = self.source
        return data


def normalize_paper_dict(
    raw: Mapping[str, Any],
    source: str | None = None,
    discipline: str = "generic",
) -> dict[str, Any]:
    return PaperRecord.from_raw(raw, source=source, discipline=discipline).to_dict()


def paper_identity(paper: Mapping[str, Any]) -> str:
    if paper.get("record_id"):
        return str(paper["record_id"])

    return build_record_id(
        paper.get("title", ""),
        paper.get("year"),
        paper.get("doi", ""),
        paper.get("arxiv_id", ""),
    )


def paper_quality_score(paper: Mapping[str, Any]) -> tuple[int, int, int, int]:
    completeness_fields = (
        "abstract",
        "venue",
        "doi",
        "arxiv_id",
        "s2_id",
        "url",
        "pdf_url",
        "local_path",
        "content_path",
        "content_json_path",
        "content_crawled",
        "content_source",
        "content_excerpt",
        "content_word_count",
        "content_updated_at",
        "citation_count",
    )
    completeness = sum(1 for field in completeness_fields if _value_present(paper.get(field)))
    verified_bonus = 1 if paper.get("verified") else 0
    downloaded_bonus = 1 if paper.get("downloaded") or paper.get("local_path") else 0
    source_rank = int(paper.get("source_rank") or SOURCE_PRIORITY.get(normalize_source_name(paper.get("source")), 0))
    return verified_bonus, downloaded_bonus, completeness, source_rank


def merge_paper_dicts(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    left = normalize_paper_dict(existing, source=existing.get("source") or existing.get("_source"))
    right = normalize_paper_dict(incoming, source=incoming.get("source") or incoming.get("_source"))

    preferred = right if paper_quality_score(right) >= paper_quality_score(left) else left
    secondary = left if preferred is right else right
    merged = dict(preferred)

    for key, value in secondary.items():
        if key in {"authors", "sources", "metadata", "_source"}:
            continue
        if not _value_present(merged.get(key)) and _value_present(value):
            merged[key] = value

    merged["authors"] = list(dict.fromkeys(left.get("authors", []) + right.get("authors", [])))

    merged_sources = set(left.get("sources", [])) | set(right.get("sources", []))
    if left.get("source"):
        merged_sources.add(left["source"])
    if right.get("source"):
        merged_sources.add(right["source"])
    merged["sources"] = sorted(source for source in merged_sources if source)

    merged["verified"] = bool(left.get("verified") or right.get("verified"))
    merged["downloaded"] = bool(left.get("downloaded") or right.get("downloaded") or merged.get("local_path"))
    merged["source_rank"] = max(
        int(left.get("source_rank") or 0),
        int(right.get("source_rank") or 0),
        *[SOURCE_PRIORITY.get(source, 0) for source in merged["sources"]],
    )
    merged["metadata"] = {**left.get("metadata", {}), **right.get("metadata", {})}
    merged["record_id"] = paper_identity(merged)
    merged["source"] = normalize_source_name(merged.get("source"))
    merged["_source"] = merged["source"]
    return merged


def dedupe_papers(papers: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}

    for paper in papers:
        normalized = normalize_paper_dict(
            paper,
            source=paper.get("source") or paper.get("_source"),
            discipline=paper.get("discipline", "generic"),
        )
        key = paper_identity(normalized)
        existing = deduped.get(key)
        deduped[key] = merge_paper_dicts(existing, normalized) if existing else normalized

    return sorted(
        deduped.values(),
        key=lambda paper: (
            parse_year(paper.get("year")) or 0,
            int(paper.get("citation_count") or 0),
            paper.get("title", "").lower(),
        ),
        reverse=True,
    )
