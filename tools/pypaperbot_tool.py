"""PyPaperBot 批量下载工具
封装 PyPaperBot CLI，支持通过 Google Scholar/CrossRef/Sci-Hub/SciDB 批量下载论文。
可下载闭源论文（经 Sci-Hub 通道）。
安装: pip install PyPaperBot
"""

import subprocess
import sys
import os
from pathlib import Path


def search_and_download(query: str, output_dir: str = "papers", limit: int = 10,
                        min_year: int | None = None,
                        scholar_pages: int = 1,
                        use_doi_file: bool = False) -> dict:
    """通过关键词搜索并下载论文

    Args:
        query: 搜索关键词
        output_dir: PDF 保存目录
        limit: 下载数量上限
        min_year: 最早年份过滤
        scholar_pages: Google Scholar 搜索页数
        use_doi_file: query 是否为 DOI 文件路径
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    cmd = ["python", "-m", "PyPaperBot"]

    if use_doi_file:
        cmd += ["--doi-file", query]
    else:
        cmd += ["--query", query]

    cmd += [
        "--dwn-dir", output_dir,
        "--limit", str(limit),
        "--scholar-pages", str(scholar_pages),
    ]

    if min_year:
        cmd += ["--min-year", str(min_year)]

    return _run_cmd(cmd)


def download_by_doi(doi: str, output_dir: str = "papers") -> dict:
    """通过单个 DOI 下载论文"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    cmd = [
        "python", "-m", "PyPaperBot",
        "--doi", doi,
        "--dwn-dir", output_dir,
    ]

    return _run_cmd(cmd)


def download_by_doi_file(doi_file: str, output_dir: str = "papers") -> dict:
    """通过 DOI 列表文件批量下载

    Args:
        doi_file: 文本文件路径，每行一个 DOI
        output_dir: PDF 保存目录
    """
    if not os.path.exists(doi_file):
        return {"success": False, "error": f"文件不存在: {doi_file}"}

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    cmd = [
        "python", "-m", "PyPaperBot",
        "--doi-file", doi_file,
        "--dwn-dir", output_dir,
    ]

    return _run_cmd(cmd)


def create_doi_file(dois: list[str], filepath: str = "dois.txt") -> str:
    """从 DOI 列表创建输入文件"""
    with open(filepath, "w", encoding="utf-8") as f:
        for doi in dois:
            f.write(doi.strip() + "\n")
    return filepath


def _run_cmd(cmd: list[str]) -> dict:
    """执行 PyPaperBot 命令"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        return {"success": False, "error": "PyPaperBot 未安装，请运行: pip install PyPaperBot"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "下载超时（5分钟）"}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python pypaperbot_tool.py search <关键词>     # 搜索并下载")
        print("  python pypaperbot_tool.py doi <DOI>           # 按 DOI 下载")
        print("  python pypaperbot_tool.py file <DOI文件>      # 批量下载")
        sys.exit(1)

    mode = sys.argv[1]
    arg = " ".join(sys.argv[2:])

    if mode == "search":
        result = search_and_download(arg)
    elif mode == "doi":
        result = download_by_doi(arg)
    elif mode == "file":
        result = download_by_doi_file(arg)
    else:
        print(f"未知模式: {mode}")
        sys.exit(1)

    if result["success"]:
        print("下载完成")
        print(result.get("stdout", ""))
    else:
        print(f"下载失败: {result.get('error', result.get('stderr', ''))}")
