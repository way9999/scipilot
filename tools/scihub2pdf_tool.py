"""Sci-Hub / LibGen PDF 下载工具
封装 scihub2pdf CLI，通过 DOI 或标题从 Sci-Hub/LibGen/arXiv 下载论文。
Sci-Hub 域名经常变化，如遇连接问题需手动更新。
安装: pip install scihub2pdf
"""

import subprocess
import sys
import os
from pathlib import Path


def download_by_doi(doi: str, output_dir: str = "papers",
                    prefer_libgen: bool = False) -> dict:
    """通过 DOI 下载论文 PDF

    Args:
        doi: 论文 DOI
        output_dir: 保存目录
        prefer_libgen: 优先使用 LibGen（无验证码但较慢）
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    cmd = ["scihub2pdf", "-d", output_dir, doi]
    if prefer_libgen:
        cmd.insert(1, "-l")

    return _run_cmd(cmd)


def download_by_title(title: str, output_dir: str = "papers") -> dict:
    """通过论文标题下载 PDF"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    cmd = ["scihub2pdf", "-d", output_dir, "-t", title]
    return _run_cmd(cmd)


def download_by_bibtex(bib_file: str, output_dir: str = "papers") -> dict:
    """通过 BibTeX 文件批量下载

    Args:
        bib_file: BibTeX 文件路径
        output_dir: 保存目录
    """
    if not os.path.exists(bib_file):
        return {"success": False, "error": f"文件不存在: {bib_file}"}

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    cmd = ["scihub2pdf", "-d", output_dir, "-b", bib_file]
    return _run_cmd(cmd)


def batch_download_dois(dois: list[str], output_dir: str = "papers") -> list[dict]:
    """批量通过 DOI 下载"""
    results = []
    for doi in dois:
        result = download_by_doi(doi, output_dir)
        result["doi"] = doi
        results.append(result)
    return results


def _run_cmd(cmd: list[str]) -> dict:
    """执行 scihub2pdf 命令"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        return {"success": False, "error": "scihub2pdf 未安装，请运行: pip install scihub2pdf"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "下载超时（2分钟），Sci-Hub 可能无法访问"}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python scihub2pdf_tool.py doi <DOI>          # 按 DOI 下载")
        print("  python scihub2pdf_tool.py title <标题>        # 按标题下载")
        print("  python scihub2pdf_tool.py bib <BibTeX文件>    # 批量下载")
        sys.exit(1)

    mode = sys.argv[1]
    arg = " ".join(sys.argv[2:])

    if mode == "doi":
        result = download_by_doi(arg)
    elif mode == "title":
        result = download_by_title(arg)
    elif mode == "bib":
        result = download_by_bibtex(arg)
    else:
        print(f"未知模式: {mode}")
        sys.exit(1)

    if result["success"]:
        print("下载完成")
        print(result.get("stdout", ""))
    else:
        print(f"下载失败: {result.get('error', result.get('stderr', ''))}")
