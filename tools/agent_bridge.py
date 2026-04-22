"""Agent Bridge — unified interface for invoking external AI coding agents.

Supports Claude Code CLI, OpenAI Codex CLI, and custom CLI agents.
Used by the paper generation pipeline at three stages:
  1. Deep code analysis (project_paper_context.py)
  2. Fix + supplement experiment data (project_runner.py)
  3. Paper-code consistency review (paper_writer.py)
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _agent_config() -> dict[str, Any]:
    """Load agent config from environment variables (injected by Rust sidecar)."""
    return {
        "enabled": os.environ.get("SCIPILOT_AGENT_ENABLED", "").lower() in ("true", "1"),
        "type": os.environ.get("SCIPILOT_AGENT_TYPE", "claude_code"),
        "path": os.environ.get("SCIPILOT_AGENT_PATH", ""),
        "max_turns": int(os.environ.get("SCIPILOT_AGENT_MAX_TURNS", "10")),
        "timeout": int(os.environ.get("SCIPILOT_AGENT_TIMEOUT", "300")),
        "auto_fix": os.environ.get("SCIPILOT_AGENT_AUTO_FIX", "true").lower() in ("true", "1"),
        "auto_supplement": os.environ.get("SCIPILOT_AGENT_AUTO_SUPPLEMENT", "true").lower() in ("true", "1"),
    }


def agent_enabled() -> bool:
    """Quick check if agent is enabled and CLI path is available."""
    cfg = _agent_config()
    return cfg["enabled"] and bool(cfg["path"] or _detect_cli(cfg["type"]))


# ---------------------------------------------------------------------------
# CLI detection
# ---------------------------------------------------------------------------

def _detect_cli(agent_type: str) -> str:
    """Try to find the CLI executable on PATH."""
    exe_name = {
        "claude_code": "claude",
        "codex": "codex",
    }.get(agent_type, "")
    if not exe_name:
        return ""
    try:
        result = subprocess.run(
            ["where" if os.name == "nt" else "which", exe_name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Core: run_agent_task
# ---------------------------------------------------------------------------

def run_agent_task(
    task: str,
    project_path: str,
    agent_config: dict[str, Any] | None = None,
    *,
    max_turns: int | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Invoke an external AI coding agent to execute a task.

    Returns:
        {
            "success": bool,
            "output": str,
            "files_modified": list[str],
            "error": str | None,
        }
    """
    cfg = agent_config or _agent_config()
    agent_type = cfg.get("type", "claude_code")
    agent_path = cfg.get("path") or _detect_cli(agent_type)

    if not agent_path:
        return {"success": False, "output": "", "files_modified": [], "error": "Agent CLI not found"}

    turns = max_turns or cfg.get("max_turns", 10)
    secs = timeout or cfg.get("timeout", 300)
    project_path = str(Path(project_path).resolve())

    # Snapshot files before agent runs to detect modifications
    files_before = _snapshot_files(project_path)

    try:
        if agent_type == "claude_code":
            result = _run_claude_code(agent_path, task, project_path, turns, secs)
        elif agent_type == "codex":
            result = _run_codex(agent_path, task, project_path, secs)
        else:
            result = _run_custom(agent_path, task, project_path, secs)
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "files_modified": [], "error": f"Agent timed out after {secs}s"}
    except Exception as exc:
        return {"success": False, "output": "", "files_modified": [], "error": str(exc)}

    # Detect modified files
    files_after = _snapshot_files(project_path)
    modified = sorted(files_after - files_before)
    result["files_modified"] = modified

    return result


# ---------------------------------------------------------------------------
# Agent-specific runners
# ---------------------------------------------------------------------------

def _run_claude_code(agent_path: str, task: str, cwd: str, max_turns: int, timeout: int) -> dict[str, Any]:
    cmd = [
        agent_path,
        "-p", task,
        "--output-format", "json",
        "--max-turns", str(max_turns),
        "--allowedTools", "Bash Edit Write Read Glob Grep",
        f"--add-dir={cwd}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)

    if proc.returncode != 0:
        return {"success": False, "output": proc.stderr or proc.stdout, "error": f"Exit code {proc.returncode}"}

    try:
        data = json.loads(proc.stdout)
        return {
            "success": True,
            "output": data.get("result", proc.stdout),
        }
    except json.JSONDecodeError:
        return {"success": True, "output": proc.stdout}


def _run_codex(agent_path: str, task: str, cwd: str, timeout: int) -> dict[str, Any]:
    cmd = [
        agent_path,
        "exec", task,
        "--json",
        "--full-auto",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)

    if proc.returncode != 0 and not proc.stdout.strip():
        return {"success": False, "output": proc.stderr, "error": f"Exit code {proc.returncode}"}

    # Parse JSONL output — collect the last assistant message
    output_parts = []
    for line in proc.stdout.strip().splitlines():
        try:
            event = json.loads(line)
            if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "message":
                for content in event["item"].get("content", []):
                    if content.get("type") == "output_text":
                        output_parts.append(content.get("text", ""))
        except json.JSONDecodeError:
            continue

    return {"success": proc.returncode == 0, "output": "\n".join(output_parts) or proc.stdout}


def _run_custom(agent_path: str, task: str, cwd: str, timeout: int) -> dict[str, Any]:
    cmd = [agent_path, task]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return {
        "success": proc.returncode == 0,
        "output": proc.stdout,
        "error": proc.stderr if proc.returncode != 0 else None,
    }


# ---------------------------------------------------------------------------
# File snapshot for change detection
# ---------------------------------------------------------------------------

def _snapshot_files(project_path: str) -> set[str]:
    """Return a set of relative file paths under the project directory."""
    root = Path(project_path)
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv", "env", ".idea", ".vscode"}
    paths = set()
    try:
        for p in root.rglob("*"):
            if p.is_file() and not any(part in skip for part in p.relative_to(root).parts):
                paths.add(str(p.relative_to(root)))
    except Exception:
        pass
    return paths


# ---------------------------------------------------------------------------
# Results sufficiency check
# ---------------------------------------------------------------------------

def results_sufficient(collected: dict[str, Any], project_context: dict[str, Any] | None = None) -> bool:
    """Check if collected results are sufficient for paper writing.

    Not just "has something" but "has what a paper needs":
    - At least 2 figures (most papers need comparison/results figures)
    - Or at least 1 CSV + 1 figure
    - Or substantial metrics data (5+ numeric results)
    If project_context has a figure_table_budget, use that as the standard.
    """
    figures = collected.get("figures", [])
    csv_files = collected.get("csv_files", [])
    metrics = collected.get("metrics", {})

    # Check against figure_table_budget if available
    budget = None
    if project_context and isinstance(project_context.get("figure_table_budget"), str):
        budget = project_context["figure_table_budget"]

    has_figures = len(figures) >= 2
    has_mixed = len(figures) >= 1 and len(csv_files) >= 1
    has_metrics = len(metrics) >= 5

    sufficient = has_figures or has_mixed or has_metrics

    # If budget hints at needed figures, check more strictly
    if budget and "图" in budget or budget and "figure" in budget.lower():
        import re
        nums = re.findall(r"(\d+)\s*(?:张图|figures?|个图)", budget, re.IGNORECASE)
        if nums:
            needed = int(nums[0])
            sufficient = sufficient and len(figures) >= needed

    return sufficient


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_analysis_prompt(context: dict[str, Any]) -> str:
    return f"""你是一个科研代码分析助手。请深入分析以下项目代码，提取：
1. 核心算法/方法名称和功能描述
2. 实验流程和评估指标
3. 关键结果和数值
4. 项目的技术创新点

项目目录：{context.get('source_project_path', '')}
技术栈：{context.get('stack', 'unknown')}
已有摘要：{context.get('project_summary', '')}
源文件列表：{', '.join(context.get('candidate_source_files', [])[:10])}
结果文件列表：{', '.join(context.get('candidate_result_files', [])[:10])}

请以 JSON 格式输出：
{{
  "methods": [{{"name": "...", "description": "..."}}],
  "experiments": [{{"name": "...", "metrics": [...]}}],
  "key_results": ["..."],
  "innovations": ["..."],
  "enhanced_summary": "一段 2-3 句话的项目总结"
}}"""


def build_fix_prompt(run_result: dict[str, Any], project_type: str, entry_script: str | None = None) -> str:
    return f"""项目运行失败，请修复错误。
项目类型：{project_type}
入口脚本：{entry_script or 'auto-detected'}
错误输出（最后2000字符）：
{run_result.get('stderr', '')[-2000:]}
标准输出（最后1000字符）：
{run_result.get('stdout', '')[-1000:]}

请只修复导致失败的错误，不要改变核心算法逻辑。
修复后确保项目能正常运行并产出结果。"""


def build_supplement_prompt(collected: dict[str, Any], project_type: str, project_context: dict[str, Any] | None = None) -> str:
    figures = [f["name"] for f in collected.get("figures", [])]
    csv_files = [f["name"] for f in collected.get("csv_files", [])]
    metrics = collected.get("metrics", {})

    budget_hint = ""
    if project_context and project_context.get("figure_table_budget"):
        budget_hint = f"\n图表需求：{project_context['figure_table_budget']}"

    return f"""项目运行成功但缺少论文所需的实验数据/图表。
项目类型：{project_type}{budget_hint}
已有图表({len(figures)}张)：{', '.join(figures[:10])}
已有CSV({len(csv_files)}个)：{', '.join(csv_files[:10])}
已有指标({len(metrics)}个)：{', '.join(list(metrics.keys())[:10])}

论文通常需要：对比实验图（柱状图/折线图）、消融实验图、性能表格等。
请检查项目代码，补充必要的数据导出和可视化代码。
要求：
1. 在 output/figures/ 下生成对比实验、消融实验等关键图表（PNG格式）
2. 在 output/results/ 下导出实验数据（CSV格式）
3. 不要修改核心算法逻辑，只补充数据记录和可视化代码
4. 确保图表有标题、坐标轴标签、图例"""


def build_consistency_prompt(result: dict[str, Any], project_context: dict[str, Any]) -> str:
    markdown_path = result.get("markdown_path", "")
    metrics = project_context.get("metrics_summary", "")
    figures = [f for f in project_context.get("candidate_result_files", []) if f.endswith((".png", ".jpg"))]

    return f"""请审查以下论文草稿与项目代码的一致性：
1. 论文中描述的实验方法是否与代码实现一致
2. 论文中引用的数值结果是否与实际实验数据匹配
3. 论文中描述的算法流程是否与代码逻辑对应
4. 论文中的图表描述是否与实际图表内容匹配

论文路径：{markdown_path}
项目目录：{project_context.get('source_project_path', '')}
实验数据：{metrics}
实际图表：{', '.join(figures[:10])}

如发现不一致，请指出具体位置并给出修正建议。以 JSON 格式输出：
{{
  "issues": [{{"location": "章节/段落位置", "description": "不一致描述", "suggestion": "修正建议"}}],
  "overall_consistency": "high|medium|low"
}}

如果一致性良好，issues 为空数组即可。"""


# ---------------------------------------------------------------------------
# Analysis result merger
# ---------------------------------------------------------------------------

def merge_agent_analysis(context: dict[str, Any], agent_output: str) -> None:
    """Parse agent analysis output and merge into project context."""
    try:
        # Try to extract JSON from agent output
        json_str = agent_output
        if "{" in agent_output:
            start = agent_output.index("{")
            end = agent_output.rindex("}") + 1
            json_str = agent_output[start:end]
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        # Can't parse JSON, add raw output as a clue
        context.setdefault("method_clues", []).append(f"[Agent analysis] {agent_output[:500]}")
        return

    # Merge methods
    for m in data.get("methods", []):
        context.setdefault("method_clues", []).append(f"{m.get('name', '?')}: {m.get('description', '')}")

    # Merge experiments
    for e in data.get("experiments", []):
        metrics_str = ", ".join(e.get("metrics", []))
        context.setdefault("result_clues", []).append(f"Experiment: {e.get('name', '?')} — metrics: {metrics_str}")

    # Merge key results
    for r in data.get("key_results", []):
        context.setdefault("result_clues", []).append(f"Key result: {r}")

    # Merge innovations
    for inn in data.get("innovations", []):
        context.setdefault("method_clues", []).append(f"Innovation: {inn}")

    # Enhance summary
    enhanced = data.get("enhanced_summary", "")
    if enhanced and len(enhanced) > len(context.get("project_summary", "")):
        context["project_summary"] = enhanced
