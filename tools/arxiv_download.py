"""arXiv 论文搜索与下载工具
基于 arxiv.py 封装，支持关键词搜索、ID 精确查询、PDF/源码下载。
arXiv API 免费无需 Key，但有速率限制（建议间隔 3s）。
"""

import arxiv
import os
import sys
import time
from pathlib import Path


def search_papers(query: str, limit: int = 10, sort_by: str = "relevance",
                  categories: list[str] | None = None) -> list[dict]:
    """搜索 arXiv 论文

    Args:
        query: 搜索关键词
        limit: 返回数量上限
        sort_by: 排序方式 "relevance" | "lastUpdatedDate" | "submittedDate"
        categories: 分类过滤，如 ["cs.AI", "cs.LG"]
    """
    sort_map = {
        "relevance": arxiv.SortCriterion.Relevance,
        "lastUpdatedDate": arxiv.SortCriterion.LastUpdatedDate,
        "submittedDate": arxiv.SortCriterion.SubmittedDate,
    }

    full_query = query
    if categories:
        cat_filter = " OR ".join(f"cat:{c}" for c in categories)
        full_query = f"({query}) AND ({cat_filter})"

    search = arxiv.Search(
        query=full_query,
        max_results=limit,
        sort_by=sort_map.get(sort_by, arxiv.SortCriterion.Relevance),
    )

    client = arxiv.Client(page_size=limit, delay_seconds=3.0, num_retries=3)
    return [_parse_result(r) for r in client.results(search)]


def get_paper(arxiv_id: str) -> dict | None:
    """按 arXiv ID 获取单篇论文详情，如 '2106.01234' 或 '2106.01234v2'"""
    search = arxiv.Search(id_list=[arxiv_id])
    client = arxiv.Client()
    results = list(client.results(search))
    if not results:
        return None
    return _parse_result(results[0])


def download_pdf(paper: dict, output_dir: str = "papers") -> str | None:
    """下载论文 PDF

    Args:
        paper: 由 search_papers 或 get_paper 返回的论文字典
        output_dir: 保存目录

    Returns:
        下载成功返回文件路径
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    first_author = paper["authors"][0].split()[-1] if paper["authors"] else "Unknown"
    year = paper.get("year", "XXXX")
    title_words = paper["title"].split()[:4]
    safe_title = "_".join(w for w in title_words if w.isalnum())
    filename = f"{first_author}_{year}_{safe_title}.pdf"
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        return filepath

    # 通过 arxiv_id 重新获取 Result 对象来下载
    search = arxiv.Search(id_list=[paper["arxiv_id"]])
    client = arxiv.Client()
    results = list(client.results(search))
    if not results:
        return None

    results[0].download_pdf(dirpath=output_dir, filename=filename)
    return filepath


def batch_search_and_download(queries: list[str], output_dir: str = "papers",
                               categories: list[str] | None = None,
                               delay: float = 3.0) -> list[dict]:
    """批量搜索并下载 arXiv 论文"""
    all_results = []
    for i, query in enumerate(queries):
        papers = search_papers(query, limit=5, categories=categories)
        for paper in papers:
            path = download_pdf(paper, output_dir)
            paper["local_path"] = path
            paper["downloaded"] = path is not None
        all_results.extend(papers)
        if i < len(queries) - 1:
            time.sleep(delay)
    return all_results


def _parse_result(result) -> dict:
    """解析 arxiv.Result 对象"""
    return {
        "arxiv_id": result.entry_id.split("/abs/")[-1],
        "title": result.title,
        "authors": [a.name for a in result.authors],
        "year": result.published.year if result.published else None,
        "abstract": result.summary,
        "categories": result.categories,
        "primary_category": result.primary_category,
        "doi": result.doi or "",
        "pdf_url": result.pdf_url,
        "comment": result.comment or "",
        "journal_ref": result.journal_ref or "",
        "url": result.entry_id,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python arxiv_download.py <搜索关键词>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    print(f"正在搜索 arXiv: {query}")
    results = search_papers(query, limit=5)
    for i, r in enumerate(results):
        print(f"\n[{i+1}] {r['title']} ({r['year']})")
        print(f"    作者: {', '.join(r['authors'][:3])}")
        print(f"    分类: {', '.join(r['categories'])}")
        print(f"    arXiv: {r['arxiv_id']}")
