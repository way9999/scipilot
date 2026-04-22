"""统一文献搜索与下载路由器
根据学科方向自动选择最优数据源组合，提供去重、回退下载等能力。
整合 CrossRef / Semantic Scholar / arXiv / PubMed / Google Scholar / Sci-Hub 等全部后端。
"""

import re
import time
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.project_models import dedupe_papers, normalize_paper_dict

# ── 学科 → 数据源路由表 ──────────────────────────────────────────────

DISCIPLINE_ROUTES = {
    "cs": {
        "label": "计算机科学/AI",
        "search": ["openalex", "ieee_xplore", "semantic_scholar", "arxiv"],
        "arxiv_cats": ["cs.AI", "cs.LG", "cs.CL", "cs.CV"],
    },
    "physics": {
        "label": "物理学",
        "search": ["openalex", "arxiv", "semantic_scholar"],
        "arxiv_cats": ["physics", "hep-th", "cond-mat", "quant-ph"],
    },
    "bio": {
        "label": "生物/生命科学",
        "search": ["openalex", "semantic_scholar", "pubmed", "crossref"],
        "arxiv_cats": ["q-bio"],
    },
    "chemistry": {
        "label": "化学/化工",
        "search": ["openalex", "semantic_scholar", "crossref"],
        "arxiv_cats": [],
    },
    "materials": {
        "label": "材料科学",
        "search": ["openalex", "semantic_scholar", "crossref", "arxiv"],
        "arxiv_cats": ["cond-mat.mtrl-sci"],
    },
    "energy": {
        "label": "能源与动力工程",
        "search": ["openalex", "semantic_scholar", "crossref"],
        "arxiv_cats": [],
    },
    "economics": {
        "label": "经济学/管理学",
        "search": ["openalex", "semantic_scholar", "crossref"],
        "arxiv_cats": ["econ", "q-fin"],
    },
}

# 下载回退链：按优先级尝试
DOWNLOAD_CHAIN = [
    "semantic_scholar",  # 开源 PDF
    "arxiv",             # arXiv 预印本
    "unpaywall",         # OA PDF 查询
    "paperscraper",      # 出版商 TDM API
    "pypaperbot",        # Sci-Hub / SciDB
    "scihub2pdf",        # Sci-Hub / LibGen
]


# ── 核心 API ─────────────────────────────────────────────────────────

def auto_search(query: str, discipline: str = "generic", limit: int = 10,
                year: str | None = None) -> list[dict]:
    """Run the routed search and normalize records before deduping."""
    route = DISCIPLINE_ROUTES.get(discipline, {})
    sources = list(route.get("search", ["openalex", "semantic_scholar", "crossref"]))
    arxiv_cats = route.get("arxiv_cats", [])

    # Auto-add Chinese sources when query contains CJK characters
    _CJK = re.compile(r'[一-鿿぀-ゟ゠-ヿ]')
    if _CJK.search(query):
        if "baidu_scholar" not in sources:
            sources.append("baidu_scholar")
        # Ensure OpenAlex is present (has some Chinese coverage)
        if "openalex" not in sources:
            sources.insert(0, "openalex")

    all_results = []

    for src in sources:
        try:
            papers = _search_source(src, query, limit, year, arxiv_cats)
            for paper in papers:
                all_results.append(normalize_paper_dict(paper, source=src, discipline=discipline))
        except Exception as e:
            print(f"[{src}] search failed: {e}")

    return dedupe_papers(all_results)


def multi_source_search(query: str, sources: list[str], limit: int = 10,
                        year: str | None = None) -> list[dict]:
    """Run explicitly selected sources and return normalized paper records."""
    all_results = []

    for src in sources:
        try:
            papers = _search_source(src, query, limit, year)
            for paper in papers:
                all_results.append(normalize_paper_dict(paper, source=src))
        except Exception as e:
            print(f"[{src}] search failed: {e}")

    return dedupe_papers(all_results)


def auto_search_and_register(query: str, discipline: str = "generic", limit: int = 10,
                             year: str | None = None, project_root: str = ".") -> list[dict]:
    """Search papers and persist them into the local project index."""
    results = auto_search(query, discipline=discipline, limit=limit, year=year)
    from tools.project_state import register_search_results

    register_search_results(results, project_root=project_root, discipline=discipline, query=query)
    return results


def auto_download(paper: dict, output_dir: str = "papers") -> str | None:
    """智能下载：按回退链依次尝试多个下载后端

    Args:
        paper: 论文字典（需包含 title，可选 doi/arxiv_id/pdf_url）
        output_dir: 保存目录

    Returns:
        成功返回文件路径，全部失败返回 None
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    for backend in DOWNLOAD_CHAIN:
        try:
            path = _download_source(backend, paper, output_dir)
            if path and os.path.exists(path) and os.path.getsize(path) > 1000:
                return path
        except Exception as e:
            print(f"[{backend}] 下载失败: {e}")

    return None


def batch_search_and_download(queries: list[str], discipline: str = "generic",
                               output_dir: str = "papers",
                               delay: float = 2.0) -> list[dict]:
    """批量搜索并下载"""
    all_results = []
    for i, query in enumerate(queries):
        papers = auto_search(query, discipline=discipline, limit=5)
        for paper in papers:
            path = auto_download(paper, output_dir)
            paper["local_path"] = path
            paper["downloaded"] = path is not None
        all_results.extend(papers)
        if i < len(queries) - 1:
            time.sleep(delay)
    return all_results


def verify_paper(title: str, authors: list[str] | None = None,
                 year: int | None = None) -> dict:
    """多源交叉验证论文真实性

    依次查询 CrossRef → Semantic Scholar → Google Scholar，
    任一源验证通过即返回。
    """
    # 1. CrossRef（最权威）
    try:
        from tools.crossref_search import verify_paper as cr_verify
        result = cr_verify(title, authors, year)
        if result.get("verified"):
            result["verified_by"] = "crossref"
            return result
    except Exception:
        pass

    # 2. Semantic Scholar
    try:
        from tools.semantic_scholar import search_papers as s2_search
        papers = s2_search(title, limit=3)
        for p in papers:
            if _title_match(title, p.get("title", "")):
                return {"verified": True, "match": p, "verified_by": "semantic_scholar"}
    except Exception:
        pass

    # 3. Google Scholar（最后手段）
    try:
        from tools.scholarly_search import search_papers as gs_search
        papers = gs_search(title, limit=3)
        for p in papers:
            if _title_match(title, p.get("title", "")):
                return {"verified": True, "match": p, "verified_by": "google_scholar"}
    except Exception:
        pass

    return {"verified": False, "reason": "所有数据源均未找到匹配论文"}


# ── 内部路由实现 ──────────────────────────────────────────────────────

def _search_source(src: str, query: str, limit: int,
                   year: str | None = None,
                   arxiv_cats: list[str] | None = None) -> list[dict]:
    """路由到具体搜索后端"""

    if src == "semantic_scholar":
        from tools.semantic_scholar import search_papers
        return search_papers(query, limit=limit, year=year)

    elif src == "openalex":
        from tools.openalex_search import search_papers as oa_search
        return oa_search(query, limit=limit, year=year)

    elif src == "baidu_scholar":
        from tools.baidu_scholar import search_papers as bs_search
        return bs_search(query, limit=limit)

    elif src == "crossref":
        from tools.crossref_search import search_by_title
        return search_by_title(query, rows=limit)

    elif src == "ieee_xplore":
        from tools.ieee_xplore import search_papers as ieee_search, has_api_key
        if has_api_key():
            return ieee_search(query, limit=limit, year=year)
        return []

    elif src == "arxiv":
        from tools.arxiv_download import search_papers
        return search_papers(query, limit=limit, categories=arxiv_cats or None)

    elif src == "scholarly":
        # Prefer SerpAPI (stable), fall back to scholarly (unstable)
        from tools.serpapi_scholar import search_papers as serpapi_search, has_api_key
        if has_api_key():
            return serpapi_search(query, limit=limit)
        from tools.scholarly_search import search_papers, setup_proxy
        setup_proxy()
        year_low = int(year.split("-")[0]) if year and "-" in year else None
        year_high = int(year.split("-")[1]) if year and "-" in year and year.split("-")[1] else None
        return search_papers(query, limit=limit, year_low=year_low, year_high=year_high)

    elif src in ("pubmed", "biorxiv", "medrxiv", "chemrxiv"):
        from tools.paperscraper_tool import search_pubmed, search_preprints
        if src == "pubmed":
            return search_pubmed(query, limit=limit)
        else:
            return search_preprints(query, sources=[src], limit=limit)

    return []


def _download_source(backend: str, paper: dict, output_dir: str) -> str | None:
    """路由到具体下载后端"""

    if backend == "semantic_scholar":
        pdf_url = paper.get("pdf_url")
        if not pdf_url:
            return None
        from tools.semantic_scholar import download_pdf
        return download_pdf(paper, output_dir)

    elif backend == "arxiv":
        arxiv_id = paper.get("arxiv_id")
        if not arxiv_id:
            return None
        from tools.arxiv_download import download_pdf, get_paper
        p = get_paper(arxiv_id)
        if p:
            return download_pdf(p, output_dir)
        return None

    elif backend == "unpaywall":
        doi = paper.get("doi")
        if not doi:
            return None
        from tools.unpaywall_download import download_pdf
        return download_pdf(doi, output_dir)

    elif backend == "paperscraper":
        doi = paper.get("doi")
        if not doi:
            return None
        from tools.paperscraper_tool import download_pdf
        return download_pdf(doi, output_dir)

    elif backend == "pypaperbot":
        doi = paper.get("doi")
        if not doi:
            return None
        from tools.pypaperbot_tool import download_by_doi
        result = download_by_doi(doi, output_dir)
        if result.get("success"):
            # PyPaperBot 不返回路径，尝试在目录中找到新文件
            return _find_latest_pdf(output_dir)
        return None

    elif backend == "scihub2pdf":
        doi = paper.get("doi")
        if not doi:
            return None
        from tools.scihub2pdf_tool import download_by_doi
        result = download_by_doi(doi, output_dir)
        if result.get("success"):
            return _find_latest_pdf(output_dir)
        return None

    return None


def _title_match(a: str, b: str) -> bool:
    """简单标题匹配"""
    a, b = a.lower().strip(), b.lower().strip()
    return a in b or b in a


def _find_latest_pdf(directory: str) -> str | None:
    """找到目录中最新的 PDF 文件"""
    pdfs = sorted(Path(directory).glob("*.pdf"), key=os.path.getmtime, reverse=True)
    return str(pdfs[0]) if pdfs else None


# ── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python unified_search.py <query>")
        print("  python unified_search.py <query> --discipline cs")
        print("  python unified_search.py <query> --download")
        print("  python unified_search.py <query> --save")
        print()
        print("Disciplines: cs, physics, bio, chemistry, materials, energy, economics")
        sys.exit(1)

    args = sys.argv[1:]
    discipline = "generic"
    do_download = False
    do_save = False
    query_parts = []

    i = 0
    while i < len(args):
        if args[i] == "--discipline" and i + 1 < len(args):
            discipline = args[i + 1]
            i += 2
        elif args[i] == "--download":
            do_download = True
            i += 1
        elif args[i] == "--save":
            do_save = True
            i += 1
        else:
            query_parts.append(args[i])
            i += 1

    query = " ".join(query_parts)
    route = DISCIPLINE_ROUTES.get(discipline, {})
    label = route.get("label", "generic")
    sources = route.get("search", ["semantic_scholar", "crossref"])

    print(f"Discipline: {label}")
    print(f"Sources: {', '.join(sources)}")
    print(f"Query: {query}")
    print()

    results = auto_search(query, discipline=discipline, limit=5)
    for i, record in enumerate(results):
        pdf_tag = "pdf" if record.get("pdf_url") else "no-pdf"
        print(f"[{i+1}] [{record.get('_source', '?')}] {record.get('title', '?')} ({record.get('year', '?')}) [{pdf_tag}]")
        authors = record.get("authors", [])
        if authors:
            print(f"    Authors: {', '.join(authors[:3])}")

    if do_download and results:
        print()
        print("Downloading...")
        for record in results:
            pdf_path = auto_download(record)
            if pdf_path:
                print(f"  ok {pdf_path}")
                record["local_path"] = pdf_path
                record["downloaded"] = True
            else:
                print(f"  fail {record.get('title', '?')[:50]}")

    if do_save and results:
        from tools.project_state import register_search_results

        merged = register_search_results(results, discipline=discipline, query=query)
        print()
        print(f"Saved {len(merged)} records to knowledge-base/paper_index.json")
