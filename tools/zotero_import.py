"""Zotero 文献导入工具
支持两种方式:
1. 通过 Zotero 本地 API (需要 Zotero 运行中，端口 23119)
2. 生成 BibTeX/RIS 文件供手动导入

推荐方式: 先尝试本地 API，失败则回退到文件导入。
"""

import requests
import json
import os
import sys
from pathlib import Path

ZOTERO_LOCAL_URL = "http://localhost:23119/api"
ZOTERO_CONNECTOR_URL = "http://localhost:23119/connector"


def check_zotero_running() -> bool:
    """检查 Zotero 是否在运行"""
    try:
        resp = requests.get(f"{ZOTERO_CONNECTOR_URL}/ping", timeout=3)
        return resp.status_code == 200
    except requests.ConnectionError:
        return False


def import_via_connector(metadata: dict, pdf_path: str | None = None) -> bool:
    """通过 Zotero Connector 导入单篇文献

    Args:
        metadata: 论文元数据 (title, authors, year, doi, journal, url, abstract)
        pdf_path: 本地 PDF 路径 (可选)
    """
    if not check_zotero_running():
        print("Zotero 未运行，无法使用 Connector 导入")
        return False

    # 构建 Zotero item
    creators = []
    for author in metadata.get("authors", []):
        parts = author.split(",", 1)
        if len(parts) == 2:
            creators.append({"creatorType": "author", "lastName": parts[0].strip(), "firstName": parts[1].strip()})
        else:
            name_parts = author.strip().split()
            if len(name_parts) >= 2:
                creators.append({"creatorType": "author", "lastName": name_parts[-1], "firstName": " ".join(name_parts[:-1])})
            else:
                creators.append({"creatorType": "author", "lastName": author, "firstName": ""})

    item = {
        "itemType": "journalArticle",
        "title": metadata.get("title", ""),
        "creators": creators,
        "date": str(metadata.get("year", "")),
        "DOI": metadata.get("doi", ""),
        "publicationTitle": metadata.get("journal", metadata.get("venue", "")),
        "url": metadata.get("url", ""),
        "abstractNote": metadata.get("abstract", ""),
    }

    # 通过 saveItems 端点导入
    payload = {
        "items": [item],
        "uri": metadata.get("url", "https://doi.org/" + metadata.get("doi", "")),
    }

    try:
        resp = requests.post(
            f"{ZOTERO_CONNECTOR_URL}/saveItems",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if resp.status_code == 201:
            print(f"已导入: {metadata['title']}")
            return True
        else:
            print(f"导入失败 (HTTP {resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"导入异常: {e}")
        return False


def generate_bibtex_file(papers: list[dict], output_path: str = "knowledge-base/sources.bib") -> str:
    """生成 BibTeX 文件，可直接导入 Zotero"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    entries = []
    for paper in papers:
        first_author = ""
        if paper.get("authors"):
            first_author = paper["authors"][0].split(",")[0].split()[-1]
        year = paper.get("year", "XXXX")
        # 生成唯一 key
        title_word = paper.get("title", "").split()[0] if paper.get("title") else "untitled"
        key = f"{first_author}{year}_{title_word}".replace(" ", "")

        entry_type = "article"
        lines = [f"@{entry_type}{{{key},"]

        if paper.get("authors"):
            authors_str = " and ".join(paper["authors"])
            lines.append(f"    author = {{{authors_str}}},")
        lines.append(f"    title = {{{paper.get('title', '')}}},")
        if paper.get("journal") or paper.get("venue"):
            lines.append(f"    journal = {{{paper.get('journal', paper.get('venue', ''))}}},")
        lines.append(f"    year = {{{year}}},")
        if paper.get("doi"):
            lines.append(f"    doi = {{{paper['doi']}}},")
        if paper.get("url"):
            lines.append(f"    url = {{{paper['url']}}},")
        if paper.get("abstract"):
            abstract = paper["abstract"].replace("{", "\\{").replace("}", "\\}")
            lines.append(f"    abstract = {{{abstract}}},")
        lines.append("}")
        entries.append("\n".join(lines))

    content = "\n\n".join(entries)

    # 追加模式：如果文件已存在，追加新条目
    mode = "a" if os.path.exists(output_path) else "w"
    with open(output_path, mode, encoding="utf-8") as f:
        if mode == "a":
            f.write("\n\n")
        f.write(content)

    print(f"BibTeX 已写入: {output_path} ({len(entries)} 条)")
    return output_path


def generate_ris_file(papers: list[dict], output_path: str = "knowledge-base/sources.ris") -> str:
    """生成 RIS 文件（Zotero 导入兼容性更好）"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    entries = []
    for paper in papers:
        lines = ["TY  - JOUR"]
        lines.append(f"TI  - {paper.get('title', '')}")
        for author in paper.get("authors", []):
            lines.append(f"AU  - {author}")
        if paper.get("year"):
            lines.append(f"PY  - {paper['year']}")
        if paper.get("journal") or paper.get("venue"):
            lines.append(f"JO  - {paper.get('journal', paper.get('venue', ''))}")
        if paper.get("doi"):
            lines.append(f"DO  - {paper['doi']}")
        if paper.get("url"):
            lines.append(f"UR  - {paper['url']}")
        if paper.get("abstract"):
            lines.append(f"AB  - {paper['abstract']}")
        lines.append("ER  - ")
        entries.append("\n".join(lines))

    content = "\n\n".join(entries)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"RIS 已写入: {output_path} ({len(entries)} 条)")
    return output_path


def batch_import(papers: list[dict], pdf_dir: str | None = None) -> dict:
    """批量导入文献到 Zotero

    优先使用 Connector API，失败则生成 BibTeX 文件。
    """
    stats = {"connector_ok": 0, "connector_fail": 0, "bibtex_generated": False}

    if check_zotero_running():
        for paper in papers:
            pdf_path = None
            if pdf_dir and paper.get("local_path"):
                pdf_path = paper["local_path"]
            success = import_via_connector(paper, pdf_path)
            if success:
                stats["connector_ok"] += 1
            else:
                stats["connector_fail"] += 1
            import time
            time.sleep(1)  # 避免过快请求
    else:
        print("Zotero 未运行，将生成 BibTeX 文件供手动导入")

    # 始终生成 BibTeX 作为备份
    generate_bibtex_file(papers)
    stats["bibtex_generated"] = True

    return stats


if __name__ == "__main__":
    # 测试: 从 JSON 文件读取论文列表并导入
    if len(sys.argv) < 2:
        print("用法: python zotero_import.py <papers.json>")
        print("JSON 格式: [{\"title\": \"...\", \"authors\": [...], \"year\": 2024, ...}]")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        papers = json.load(f)

    stats = batch_import(papers)
    print(f"\n导入统计: {json.dumps(stats, ensure_ascii=False)}")
