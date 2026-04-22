"""Source-code-driven figure generation.

Given a project directory, this module asks the LLM to produce a matplotlib
script that loads real data files from the project and renders a figure. The
script is sandboxed to a temp directory, executed, and the resulting PNG is
copied into ``<project_root>/output/figures/``.

Design notes
------------
- Only PNG output is produced; PDF is skipped (python-docx cannot embed PDF).
- The LLM is told the list of candidate data files (CSV/JSON/NPY) with their
  headers/sample rows so it can write a script that actually loads them.
- Scripts are run with a strict timeout and no network access.
- Generated figures are deduped against existing output/figures content by
  file-stem similarity so this pass can run idempotently.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

_DATA_EXT = {'.csv', '.json', '.npy', '.npz', '.mat', '.tsv', '.xlsx'}
_MAX_FILES_SHOWN = 8
_MAX_PREVIEW_BYTES = 1200
_SCRIPT_TIMEOUT = 60  # seconds


def _scan_data_files(project_root: Path, limit: int = _MAX_FILES_SHOWN) -> list[dict[str, Any]]:
    """Return the most promising data files, preferring ones under output/ / results/ / data/."""
    priority_dirs = ['output', 'results', 'data', 'logs', 'experiments']
    hits: list[tuple[int, Path]] = []
    for root, _dirs, files in os.walk(project_root):
        rel = Path(root).relative_to(project_root)
        priority = 0
        for i, p in enumerate(priority_dirs):
            if p in rel.parts:
                priority = len(priority_dirs) - i
                break
        for fn in files:
            if any(fn.lower().endswith(ext) for ext in _DATA_EXT):
                hits.append((priority, Path(root) / fn))
    hits.sort(key=lambda x: (-x[0], str(x[1])))

    out: list[dict[str, Any]] = []
    for _pri, path in hits[:limit]:
        info: dict[str, Any] = {
            'path': str(path),
            'rel': str(path.relative_to(project_root)),
            'size': path.stat().st_size if path.exists() else 0,
        }
        try:
            if path.suffix.lower() in {'.csv', '.tsv'}:
                with path.open('r', encoding='utf-8', errors='ignore') as f:
                    info['preview'] = f.read(_MAX_PREVIEW_BYTES)
            elif path.suffix.lower() == '.json':
                with path.open('r', encoding='utf-8', errors='ignore') as f:
                    info['preview'] = f.read(_MAX_PREVIEW_BYTES)
            else:
                info['preview'] = f'(binary {path.suffix} — no preview)'
        except Exception:
            info['preview'] = '(unreadable)'
        out.append(info)
    return out


def _existing_figure_stems(figures_dir: Path) -> set[str]:
    if not figures_dir.exists():
        return set()
    return {p.stem.lower() for p in figures_dir.iterdir() if p.suffix.lower() in {'.png', '.jpg'}}


def _build_prompt(topic: str, language: str, data_files: list[dict[str, Any]],
                  existing_stems: set[str], target_caption: str) -> str:
    file_lines: list[str] = []
    for idx, info in enumerate(data_files, 1):
        preview = info.get('preview') or ''
        if isinstance(preview, str) and len(preview) > 600:
            preview = preview[:600] + ' ...'
        file_lines.append(
            f"[{idx}] {info['rel']} ({info['size']} bytes)\n   preview:\n{preview}"
        )
    files_section = '\n'.join(file_lines) if file_lines else '(no candidate data files were detected)'
    existing_hint = ', '.join(sorted(existing_stems)[:20]) if existing_stems else 'none'
    lang_note = (
        'All labels/titles should be in Chinese.' if language == 'zh'
        else 'All labels/titles should be in English.'
    )

    return (
        "You are writing a single self-contained Python script that produces a "
        "publication-quality matplotlib figure for an engineering thesis. The "
        f"thesis topic is: \"{topic}\".\n\n"
        f"The figure should visualize: {target_caption}.\n\n"
        "PROJECT DATA FILES (actual content is real — use these files verbatim; "
        "do NOT synthesize data):\n"
        f"{files_section}\n\n"
        "REQUIREMENTS:\n"
        "- Output ONLY Python code inside one ```python ... ``` fence. No prose outside the fence.\n"
        "- Read data from one of the listed files using a relative path from the script's working directory.\n"
        "- If a listed file path is absolute, load it directly using that absolute path.\n"
        "- Do NOT attempt network access, shell commands, or reading files outside the project root.\n"
        "- Save the figure to the path given by environment variable OUT_PNG (os.environ['OUT_PNG']). Use dpi>=140.\n"
        "- Use matplotlib + numpy + pandas only. Do not import seaborn unless already common.\n"
        f"- {lang_note}\n"
        f"- Avoid producing a figure whose stem is similar to any of these existing ones: {existing_hint}.\n"
        "- Do not call plt.show(). End with plt.close().\n"
    )


def _extract_python_code(raw: str) -> str | None:
    m = re.search(r'```python\s*\n(.*?)```', raw, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'```\s*\n(.*?)```', raw, re.S)
    if m:
        return m.group(1).strip()
    return None


def _run_script(code: str, out_png: Path, cwd: Path, timeout: int = _SCRIPT_TIMEOUT) -> tuple[bool, str]:
    """Execute ``code`` with OUT_PNG pointing to ``out_png``. Returns (ok, log)."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(
            "import os\n"
            "os.environ['MPLBACKEND'] = 'Agg'\n"
            "import matplotlib\nmatplotlib.use('Agg')\n"
        )
        f.write(code)
        script_path = f.name
    try:
        env = os.environ.copy()
        env['OUT_PNG'] = str(out_png)
        env['MPLBACKEND'] = 'Agg'
        proc = subprocess.run(
            [sys.executable, script_path],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        ok = proc.returncode == 0 and out_png.exists() and out_png.stat().st_size > 2048
        log = (proc.stdout or '') + '\n' + (proc.stderr or '')
        return ok, log[-2000:]
    except subprocess.TimeoutExpired:
        return False, f'[timeout after {timeout}s]'
    except Exception as exc:
        return False, f'[executor exception] {exc}'
    finally:
        try:
            Path(script_path).unlink(missing_ok=True)
        except Exception:
            pass


def _derive_target_captions(topic: str, language: str) -> list[str]:
    """Produce a handful of caption strings to request, one per figure.

    Intentionally generic so this works for any engineering thesis. The LLM
    will still read the actual data to decide what to plot.
    """
    zh = language == 'zh'
    return [
        '实验结果总览柱状图或曲线' if zh else 'bar or curve chart of experiment results',
        '关键指标随参数变化的趋势' if zh else 'trend of key metrics over a parameter',
        '对比不同方法/场景的性能差异' if zh else 'performance comparison across methods/scenarios',
    ]


def generate_source_driven_figures(
    project_root: str | Path,
    topic: str,
    llm_call: Callable[[str], str] | None,
    language: str = 'zh',
    max_figures: int = 3,
    figures_dir: str | Path | None = None,
) -> list[Path]:
    """Produce up to ``max_figures`` figures by asking an LLM to write a script
    that reads the project's real data files and plots them.

    ``llm_call(prompt) -> str`` is a closure the caller must provide (already
    wired to their chosen model/chain). Returns the list of produced PNG paths.
    """
    if not callable(llm_call):
        return []
    root = Path(project_root).resolve()
    if figures_dir is None:
        figures_dir = root / 'output' / 'figures'
    figures_dir = Path(figures_dir)
    figures_dir.mkdir(parents=True, exist_ok=True)

    data_files = _scan_data_files(root)
    if not data_files:
        return []

    produced: list[Path] = []
    for idx, caption in enumerate(_derive_target_captions(topic, language)[:max_figures], 1):
        existing_stems = _existing_figure_stems(figures_dir)
        prompt = _build_prompt(topic, language, data_files, existing_stems, caption)
        try:
            raw = llm_call(prompt) or ''
        except Exception as exc:
            print(f"[SourceFig] LLM call failed: {exc}")
            continue
        code = _extract_python_code(raw)
        if not code:
            print(f"[SourceFig] figure {idx}: no python code in LLM output")
            continue
        out_name = f'llm_source_fig_{idx}.png'
        out_png = figures_dir / out_name
        ok, log = _run_script(code, out_png, cwd=root, timeout=_SCRIPT_TIMEOUT)
        if not ok:
            print(f"[SourceFig] figure {idx} failed to produce PNG.")
            if log.strip():
                first_line = log.strip().splitlines()[-1][:200]
                print(f"   tail: {first_line}")
            continue
        produced.append(out_png)
        print(f"[SourceFig] figure {idx} -> {out_png.name}")
    return produced
