"""多源文献搜索工具
基于 paperscraper 封装，支持 PubMed、arXiv、bioRxiv、medRxiv、chemRxiv。
特别适合生物医学方向，支持全文 PDF/XML 下载和引用数查询。
"""

import json
import sys
import os
from pathlib import Path


def search_pubmed(query: str, limit: int = 10) -> list[dict]:
    """搜索 PubMed 论文"""
    from paperscraper.pubmed import get_and_dump_pubmed_papers

    outfile = _tmp_path("pubmed_results.jsonl")
    get_and_dump_pubmed_papers(query, output_filepath=outfile, max_results=limit)
    return _load_jsonl(outfile)


def search_preprints(query: str, sources: list[str] | None = None,
                     limit: int = 10) -> list[dict]:
    """搜索预印本服务器

    Args:
        query: 搜索关键词
        sources: 数据源列表，可选 "biorxiv", "medrxiv", "chemrxiv"，默认全部
        limit: 每个源的返回数量
    """
    from paperscraper.get_dumps import biorxiv, medrxiv, chemrxiv
    from paperscraper.xrxiv.xrxiv_query import XRXivQuery

    if sources is None:
        sources = ["biorxiv", "medrxiv", "chemrxiv"]

    dump_funcs = {
        "biorxiv": biorxiv,
        "medrxiv": medrxiv,
        "chemrxiv": chemrxiv,
    }

    all_results = []
    for src in sources:
        if src not in dump_funcs:
            continue
        try:
            dump_funcs[src]()  # 下载/更新本地 dump
            querier = XRXivQuery(f"paperscraper/latest/{src}.jsonl")
            outfile = _tmp_path(f"{src}_results.jsonl")
            querier.search_keywords(
                [query.split()],
                output_filepath=outfile,
                max_results=limit,
            )
            results = _load_jsonl(outfile)
            for r in results:
                r["source"] = src
            all_results.extend(results)
        except Exception as e:
            print(f"[{src}] 搜索失败: {e}")

    return all_results


def get_citation_count(doi: str, backend: str = "semanticscholar") -> int | None:
    """获取论文引用数

    Args:
        doi: 论文 DOI
        backend: "semanticscholar" 或 "googlescholar"
    """
    from paperscraper.citations import get_citations_from_semanticscholar

    if backend == "semanticscholar":
        try:
            return get_citations_from_semanticscholar(doi)
        except Exception:
            return None
    return None


def download_pdf(doi: str, output_dir: str = "papers") -> str | None:
    """通过 DOI 下载论文 PDF（尝试开源渠道）"""
    from paperscraper.pdf import save_pdf

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    safe_doi = doi.replace("/", "_")
    filepath = os.path.join(output_dir, f"{safe_doi}.pdf")

    if os.path.exists(filepath):
        return filepath

    try:
        save_pdf({"doi": doi}, filepath=filepath)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return filepath
    except Exception:
        pass
    return None


def _tmp_path(filename: str) -> str:
    """获取临时文件路径"""
    tmp_dir = os.path.join(os.path.dirname(__file__), ".cache")
    os.makedirs(tmp_dir, exist_ok=True)
    return os.path.join(tmp_dir, filename)


def _load_jsonl(filepath: str) -> list[dict]:
    """加载 JSONL 文件并解析为标准格式"""
    results = []
    if not os.path.exists(filepath):
        return results
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                results.append({
                    "title": raw.get("title", ""),
                    "authors": raw.get("authors", []),
                    "year": raw.get("date", "")[:4] if raw.get("date") else None,
                    "abstract": raw.get("abstract", ""),
                    "doi": raw.get("doi", ""),
                    "url": raw.get("url", ""),
                    "source": raw.get("source", ""),
                })
            except json.JSONDecodeError:
                continue
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python paperscraper_tool.py <搜索关键词>")
        print("支持数据源: PubMed, bioRxiv, medRxiv, chemRxiv")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    print(f"正在搜索 PubMed: {query}")
    results = search_pubmed(query, limit=5)
    for i, r in enumerate(results):
        print(f"\n[{i+1}] {r['title']} ({r['year']})")
        print(f"    DOI: {r['doi']}")
