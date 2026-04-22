"""Semantic Scholar API 文献搜索与下载工具
支持论文搜索、详情获取、开源 PDF 下载。
免费 API 有速率限制(100次/5分钟)，申请 API Key 可提升至更高。
API Key 设置: 环境变量 S2_API_KEY
"""

import requests
import json
import sys
import os
import time
from pathlib import Path

BASE_URL = "https://api.semanticscholar.org/graph/v1"
SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "paperId,title,authors,year,abstract,venue,externalIds,openAccessPdf,citationCount,referenceCount,url"


def _headers() -> dict:
    h = {}
    api_key = os.environ.get("S2_API_KEY")
    if api_key:
        h["x-api-key"] = api_key
    return h


def search_papers(query: str, limit: int = 10, year: str | None = None,
                  fields_of_study: list[str] | None = None) -> list[dict]:
    """搜索论文，返回匹配结果列表

    Args:
        query: 搜索关键词
        limit: 返回数量上限
        year: 年份过滤，如 "2020-2024" 或 "2023-"
        fields_of_study: 学科过滤，如 ["Computer Science"]
    """
    params = {"query": query, "limit": limit, "fields": FIELDS}
    if year:
        params["year"] = year
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)

    resp = requests.get(SEARCH_URL, params=params, headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return [_parse_paper(p) for p in data.get("data", [])]


def get_paper(paper_id: str) -> dict | None:
    """获取单篇论文详情，paper_id 可以是 S2 ID、DOI、ArXiv ID 等

    支持格式:
        - Semantic Scholar ID: "649def34f8be52c8b66281af98ae884c09aef38b"
        - DOI: "DOI:10.1234/xxxxx"
        - ArXiv: "ArXiv:2106.01234"
        - ACL: "ACL:P19-1423"
    """
    url = f"{BASE_URL}/paper/{paper_id}"
    params = {"fields": FIELDS}
    resp = requests.get(url, params=params, headers=_headers(), timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _parse_paper(resp.json())


def download_pdf(paper: dict, output_dir: str = "papers") -> str | None:
    """下载开源论文 PDF

    Returns:
        下载成功返回文件路径，无开源 PDF 返回 None
    """
    pdf_url = paper.get("pdf_url")
    if not pdf_url:
        return None

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 生成文件名: FirstAuthor_Year_前几个关键词.pdf
    first_author = paper["authors"][0].split()[-1] if paper["authors"] else "Unknown"
    year = paper.get("year", "XXXX")
    title_words = paper["title"].split()[:4]
    safe_title = "_".join(w for w in title_words if w.isalnum())
    filename = f"{first_author}_{year}_{safe_title}.pdf"
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        return filepath

    resp = requests.get(pdf_url, headers={"User-Agent": "SciResearchTool/1.0"}, timeout=60, stream=True)
    resp.raise_for_status()

    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    return filepath


def batch_search_and_download(queries: list[str], output_dir: str = "papers",
                               year: str | None = None, delay: float = 2.0) -> list[dict]:
    """批量搜索并下载开源论文

    Args:
        queries: 搜索关键词列表
        output_dir: PDF 保存目录
        year: 年份过滤
        delay: 请求间隔(秒)，避免触发速率限制
    """
    all_results = []
    for i, query in enumerate(queries):
        papers = search_papers(query, limit=5, year=year)
        for paper in papers:
            path = download_pdf(paper, output_dir)
            paper["local_path"] = path
            paper["downloaded"] = path is not None
        all_results.extend(papers)
        if i < len(queries) - 1:
            time.sleep(delay)
    return all_results


def generate_bibtex(paper: dict) -> str:
    """从论文元数据生成 BibTeX"""
    first_author = paper["authors"][0].split()[-1] if paper["authors"] else "Unknown"
    year = paper.get("year", "XXXX")
    key = f"{first_author}{year}"

    lines = [f"@article{{{key},"]
    if paper["authors"]:
        lines.append(f'    author = {{{" and ".join(paper["authors"])}}},')
    lines.append(f'    title = {{{paper["title"]}}},')
    if paper.get("venue"):
        lines.append(f'    journal = {{{paper["venue"]}}},')
    lines.append(f'    year = {{{year}}},')
    doi = paper.get("doi")
    if doi:
        lines.append(f'    doi = {{{doi}}},')
    lines.append("}")
    return "\n".join(lines)


def _parse_paper(raw: dict) -> dict:
    """解析 S2 API 返回的论文数据"""
    authors = [a.get("name", "") for a in raw.get("authors", [])]
    external_ids = raw.get("externalIds", {}) or {}
    oa_pdf = raw.get("openAccessPdf") or {}

    return {
        "s2_id": raw.get("paperId", ""),
        "title": raw.get("title", ""),
        "authors": authors,
        "year": raw.get("year"),
        "abstract": raw.get("abstract", ""),
        "venue": raw.get("venue", ""),
        "doi": external_ids.get("DOI", ""),
        "arxiv_id": external_ids.get("ArXiv", ""),
        "pdf_url": oa_pdf.get("url"),
        "citation_count": raw.get("citationCount", 0),
        "url": raw.get("url", ""),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python semantic_scholar.py <搜索关键词>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    print(f"正在搜索: {query}")
    results = search_papers(query, limit=5)
    for i, r in enumerate(results):
        oa = "有PDF" if r["pdf_url"] else "无PDF"
        print(f"\n[{i+1}] {r['title']} ({r['year']}) [{oa}]")
        print(f"    作者: {', '.join(r['authors'][:3])}")
        print(f"    引用: {r['citation_count']} | DOI: {r['doi']}")
