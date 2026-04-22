"""CrossRef API 文献元数据查询工具
用于验证论文真实性：通过 DOI、标题、作者查询论文元数据。
CrossRef 是开源免费 API，无需 API Key。
"""

import requests
import json
import sys
import time

BASE_URL = "https://api.crossref.org/works"
HEADERS = {
    "User-Agent": "SciResearchTool/1.0 (mailto:research@example.com)"
}


def search_by_title(title: str, rows: int = 5) -> list[dict]:
    """按标题搜索论文，返回匹配结果列表"""
    params = {
        "query.title": title,
        "rows": rows,
        "select": "DOI,title,author,published-print,published-online,container-title,type,abstract,URL"
    }
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    items = resp.json()["message"]["items"]
    return [_parse_item(item) for item in items]


def search_by_doi(doi: str) -> dict | None:
    """按 DOI 精确查询论文元数据"""
    url = f"{BASE_URL}/{doi}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _parse_item(resp.json()["message"])


def search_by_author(author: str, rows: int = 10) -> list[dict]:
    """按作者名搜索论文"""
    params = {
        "query.author": author,
        "rows": rows,
        "select": "DOI,title,author,published-print,published-online,container-title,type"
    }
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    items = resp.json()["message"]["items"]
    return [_parse_item(item) for item in items]


def verify_paper(title: str, authors: list[str] | None = None, year: int | None = None) -> dict:
    """验证论文真实性：搜索标题并与提供的作者/年份交叉比对"""
    results = search_by_title(title, rows=5)
    if not results:
        return {"verified": False, "reason": "未在 CrossRef 中找到匹配论文", "candidates": []}

    best_match = None
    best_score = 0

    for r in results:
        score = 0
        # 标题相似度（简单包含检查）
        if title.lower() in r["title"].lower() or r["title"].lower() in title.lower():
            score += 3
        # 作者匹配
        if authors and r["authors"]:
            for a in authors:
                if any(a.lower() in ra.lower() for ra in r["authors"]):
                    score += 1
        # 年份匹配
        if year and r["year"] and abs(int(r["year"]) - year) <= 1:
            score += 2

        if score > best_score:
            best_score = score
            best_match = r

    if best_score >= 3:
        return {"verified": True, "match": best_match, "score": best_score}
    else:
        return {"verified": False, "reason": "找到候选但匹配度不足", "candidates": results[:3], "best_score": best_score}


def generate_bibtex(metadata: dict) -> str:
    """从元数据生成 BibTeX 条目"""
    first_author = metadata["authors"][0].split(",")[0].split()[-1] if metadata["authors"] else "Unknown"
    year = metadata["year"] or "XXXX"
    key = f"{first_author}{year}"

    entry_type = "article" if metadata.get("journal") else "misc"
    lines = [f"@{entry_type}{{{key},"]
    if metadata["authors"]:
        lines.append(f'    author = {{{" and ".join(metadata["authors"])}}},')
    lines.append(f'    title = {{{metadata["title"]}}},')
    if metadata.get("journal"):
        lines.append(f'    journal = {{{metadata["journal"]}}},')
    lines.append(f'    year = {{{year}}},')
    if metadata.get("doi"):
        lines.append(f'    doi = {{{metadata["doi"]}}},')
    if metadata.get("url"):
        lines.append(f'    url = {{{metadata["url"]}}},')
    lines.append("}")
    return "\n".join(lines)


def _parse_item(item: dict) -> dict:
    """解析 CrossRef API 返回的单条记录"""
    authors = []
    for a in item.get("author", []):
        name = f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
        if name:
            authors.append(name)

    date_parts = item.get("published-print", item.get("published-online", {})).get("date-parts", [[None]])
    year = date_parts[0][0] if date_parts and date_parts[0] else None

    titles = item.get("title", [""])
    title = titles[0] if titles else ""

    journals = item.get("container-title", [""])
    journal = journals[0] if journals else ""

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "doi": item.get("DOI", ""),
        "url": item.get("URL", ""),
        "type": item.get("type", ""),
        "abstract": item.get("abstract", ""),
    }


def batch_verify(papers: list[dict]) -> list[dict]:
    """批量验证论文列表，每条包含 title 和可选的 authors/year"""
    results = []
    for i, paper in enumerate(papers):
        result = verify_paper(
            title=paper["title"],
            authors=paper.get("authors"),
            year=paper.get("year")
        )
        result["input"] = paper
        results.append(result)
        if i < len(papers) - 1:
            time.sleep(1)  # 礼貌性延迟
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python crossref_search.py <论文标题>")
        sys.exit(1)

    title = " ".join(sys.argv[1:])
    print(f"正在搜索: {title}")
    results = search_by_title(title)
    for i, r in enumerate(results):
        print(f"\n--- 结果 {i+1} ---")
        print(json.dumps(r, ensure_ascii=False, indent=2))
