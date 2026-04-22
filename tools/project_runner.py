"""Project auto-runner: detect project type, execute, collect data for paper generation.

Usage:
    from tools.project_runner import run_project
    context = run_project("G:/matlab")
    # context contains: logs, figures, data_files, metrics, screenshots

Supports:
    - MATLAB (.m entry scripts)
    - Python (.py entry scripts, with venv/conda detection)
    - Generic (any executable with known output dirs)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Agent writeback policy
# ---------------------------------------------------------------------------

def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes", "on")


def _allow_agent_project_edits(explicit_opt_in: bool | None = None) -> bool:
    """Return whether agent-assisted source edits are allowed for this run."""
    if explicit_opt_in is not None:
        return explicit_opt_in
    return _env_flag("SCIPILOT_AGENT_ALLOW_PROJECT_EDITS", default=False)


# ---------------------------------------------------------------------------
# Project type detection
# ---------------------------------------------------------------------------

def detect_project_type(project_root: str | Path) -> str:
    """Detect project type by file signatures.

    Returns one of: "matlab", "python", "ros", "generic".
    """
    root = Path(project_root).resolve()

    # MATLAB: has .m files at root or in code/ subdirectory
    m_files = list(root.glob("*.m")) + list(root.glob("code/*.m"))
    if m_files:
        return "matlab"

    # ROS: has package.xml or CMakeLists.txt with roscpp/rospy
    if (root / "package.xml").exists() or (root / "catkin_ws").is_dir():
        return "ros"

    # Python: has setup.py, pyproject.toml, requirements.txt, or .py entry scripts
    py_markers = ["setup.py", "pyproject.toml", "requirements.txt", "main.py", "run.py", "app.py"]
    if any((root / m).exists() for m in py_markers):
        return "python"
    py_files = list(root.glob("*.py")) + list(root.glob("src/*.py"))
    if py_files:
        return "python"

    return "generic"


def find_entry_script(project_root: str | Path, project_type: str) -> str | None:
    """Find the main entry script for the project."""
    root = Path(project_root).resolve()

    if project_type == "matlab":
        # Look for start/main/run .m files
        for pattern in ["start*.m", "main*.m", "run*.m", "demo*.m"]:
            matches = list(root.glob(pattern))
            if matches:
                return str(matches[0])
        # Fall back to any .m in root
        m_files = list(root.glob("*.m"))
        if m_files:
            return str(m_files[0])

    elif project_type == "python":
        for name in ["main.py", "run.py", "app.py", "train.py", "test.py"]:
            if (root / name).exists():
                return str(root / name)
        # Check src/ directory
        for name in ["src/main.py", "src/run.py"]:
            if (root / name).exists():
                return str(root / name)

    elif project_type == "ros":
        for name in ["run.launch", "launch/main.launch"]:
            if (root / name).exists():
                return str(root / name)

    return None


# ---------------------------------------------------------------------------
# Project runners
# ---------------------------------------------------------------------------

def _run_matlab(project_root: Path, entry_script: str, timeout: int = 300) -> dict[str, Any]:
    """Run a MATLAB script via matlab -batch."""
    output_dir = project_root / "output"
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    results_dir = output_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Build MATLAB command that:
    # 1. Runs the entry script
    # 2. Saves all open figures as PNG
    # 3. Exports workspace variables to JSON/CSV
    wrapper_script = f"""
    try
        run('{entry_script.replace(os.sep, '/')}');

        % Save all open figures
        figs = findall(0, 'Type', 'figure');
        fig_dir = '{str(figures_dir).replace(os.sep, '/')}';
        for i = 1:numel(figs)
            saveas(figs(i), fullfile(fig_dir, ['figure_' num2str(i)]), 'png');
        end

        % Export workspace variables to JSON
        ws_vars = who;
        json_data = struct();
        result_dir = '{str(results_dir).replace(os.sep, '/')}';
        for v_idx = 1:numel(ws_vars)
            var_name = ws_vars{{v_idx}};
            var_val = eval(var_name);
            if isnumeric(var_val) && numel(var_val) <= 1000
                % Save small numeric arrays as CSV
                csvwrite(fullfile(result_dir, [var_name '.csv']), var_val);
            end
        end
    catch ME
        fprintf('MATLAB Error: %s\\n', ME.message);
    end
    """

    # Write wrapper script
    wrapper_path = project_root / "_auto_run_wrapper.m"
    wrapper_path.write_text(wrapper_script, encoding="utf-8")

    try:
        result = subprocess.run(
            ["matlab", "-batch", f"run('{str(wrapper_path).replace(os.sep, '/')}')"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(project_root),
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-5000:] if result.stdout else "",
            "stderr": result.stderr[-5000:] if result.stderr else "",
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        # matlab not on PATH — try common locations
        matlab_paths = [
            r"C:\Program Files\MATLAB\R2024b\bin\matlab.exe",
            r"C:\Program Files\MATLAB\R2024a\bin\matlab.exe",
            r"C:\Program Files\MATLAB\R2023b\bin\matlab.exe",
            r"C:\Program Files\MATLAB\R2023a\bin\matlab.exe",
        ]
        for mp in matlab_paths:
            if Path(mp).exists():
                result = subprocess.run(
                    [mp, "-batch", f"run('{str(wrapper_path).replace(os.sep, '/')}')"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(project_root),
                )
                return {
                    "success": result.returncode == 0,
                    "stdout": result.stdout[-5000:] if result.stdout else "",
                    "stderr": result.stderr[-5000:] if result.stderr else "",
                    "returncode": result.returncode,
                }
        return {
            "success": False,
            "stdout": "",
            "stderr": "MATLAB not found. Install MATLAB or add it to PATH.",
            "returncode": -1,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timeout after {timeout}s", "returncode": -1}
    finally:
        wrapper_path.unlink(missing_ok=True)


def _run_python(project_root: Path, entry_script: str, timeout: int = 300) -> dict[str, Any]:
    """Run a Python script."""
    output_dir = project_root / "output"
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    # Detect venv
    python_exe = sys.executable
    venv_candidates = [
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root / "venv" / "Scripts" / "python.exe",
        project_root / ".venv" / "bin" / "python",
        project_root / "venv" / "bin" / "python",
    ]
    for vc in venv_candidates:
        if vc.exists():
            python_exe = str(vc)
            break

    try:
        result = subprocess.run(
            [python_exe, entry_script],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(project_root),
            env={**os.environ, "MPLBACKEND": "Agg"},  # Non-interactive matplotlib
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-5000:] if result.stdout else "",
            "stderr": result.stderr[-5000:] if result.stderr else "",
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timeout after {timeout}s", "returncode": -1}


def _run_generic(project_root: Path, entry_script: str, timeout: int = 300) -> dict[str, Any]:
    """Run a generic script."""
    try:
        result = subprocess.run(
            [entry_script],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(project_root),
            shell=True,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-5000:] if result.stdout else "",
            "stderr": result.stderr[-5000:] if result.stderr else "",
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timeout after {timeout}s", "returncode": -1}


# ---------------------------------------------------------------------------
# Data collection (post-run scan)
# ---------------------------------------------------------------------------

def _collect_output_data(project_root: Path) -> dict[str, Any]:
    """Scan project output directories for generated files."""
    data: dict[str, Any] = {
        "figures": [],
        "csv_files": [],
        "json_files": [],
        "log_lines": [],
        "metrics": {},
    }

    # Scan output/figures
    fig_dir = project_root / "output" / "figures"
    if fig_dir.exists():
        for f in sorted(fig_dir.glob("*.png")):
            data["figures"].append({
                "path": str(f),
                "relative": f"output/figures/{f.name}",
                "name": f.stem,
            })
        for f in sorted(fig_dir.glob("*.jpg")):
            data["figures"].append({
                "path": str(f),
                "relative": f"output/figures/{f.name}",
                "name": f.stem,
            })

    # Scan output/results for CSV
    results_dir = project_root / "output" / "results"
    if results_dir.exists():
        for f in sorted(results_dir.glob("*.csv")):
            data["csv_files"].append({
                "path": str(f),
                "name": f.stem,
            })
        for f in sorted(results_dir.glob("*.json")):
            try:
                content = json.loads(f.read_text(encoding="utf-8"))
                data["json_files"].append({"path": str(f), "name": f.stem, "data": content})
            except Exception:
                pass

    # Also scan output/ directly for images
    output_dir = project_root / "output"
    if output_dir.exists():
        for subdir in output_dir.iterdir():
            if subdir.is_dir() and subdir.name not in ("figures", "results"):
                for f in sorted(subdir.glob("*.png")) + sorted(subdir.glob("*.jpg")):
                    # Copy to figures dir for unified access
                    fig_dir.mkdir(parents=True, exist_ok=True)
                    dest = fig_dir / f"{subdir.name}_{f.name}"
                    if not dest.exists():
                        shutil.copy2(f, dest)
                    data["figures"].append({
                        "path": str(dest),
                        "relative": f"output/figures/{dest.name}",
                        "name": f"{subdir.name}_{f.stem}",
                    })

    # Extract numeric metrics from CSV files
    for csv_info in data["csv_files"]:
        try:
            csv_path = Path(csv_info["path"])
            lines = csv_path.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) >= 2:
                headers = lines[0].split(",")
                for line in lines[1:]:
                    vals = line.split(",")
                    for h, v in zip(headers, vals):
                        h = h.strip()
                        try:
                            data["metrics"][f"{csv_info['name']}_{h}"] = float(v.strip())
                        except ValueError:
                            pass
        except Exception:
            pass

    return data


# ---------------------------------------------------------------------------
# Build project_context for paper pipeline
# ---------------------------------------------------------------------------

def _build_project_context(
    project_root: Path,
    run_result: dict[str, Any],
    collected: dict[str, Any],
) -> dict[str, Any]:
    """Build project_context dict compatible with paper_writer's generate_paper_package()."""
    context: dict[str, Any] = {}

    # Source files
    source_files = []
    for ext in ["*.m", "*.py", "*.cpp", "*.h", "*.java", "*.c"]:
        source_files.extend(str(f.relative_to(project_root)) for f in project_root.rglob(ext) if "node_modules" not in str(f))
    context["source_files"] = source_files[:30]

    # Stack
    context["stack"] = _detect_stack(project_root)

    # --- Read source files to extract real project content ---
    _src_summary = _extract_source_summary(project_root, source_files[:15])
    if _src_summary:
        context["project_summary"] = _src_summary["summary"]
        context["method_clues"] = _src_summary["methods"]
        if not context.get("result_clues"):
            context["result_clues"] = _src_summary["clues"]

    # Console output
    stdout = run_result.get("stdout", "")
    if stdout:
        context["console_output"] = stdout[-3000:]
        context.setdefault("result_clues", []).extend(_extract_clues_from_output(stdout))

    # Figures
    context["candidate_result_files"] = [
        f"output/figures/{Path(f['path']).name}" for f in collected["figures"]
    ]

    # CSV data summaries
    result_clues = list(context.get("result_clues", []))
    for csv_info in collected["csv_files"]:
        try:
            csv_path = Path(csv_info["path"])
            lines = csv_path.read_text(encoding="utf-8").strip().split("\n")
            if lines:
                header = lines[0].strip()
                rows = [l.strip() for l in lines[1:6]]
                result_clues.append(f"{csv_info['name']}: {header}; data: {'; '.join(rows)}")
        except Exception:
            pass
    context["result_clues"] = result_clues

    # Metrics
    if collected["metrics"]:
        context["experiment_metrics"] = collected["metrics"]
        metric_lines = [f"  {k}: {v}" for k, v in collected["metrics"].items()]
        context["metrics_summary"] = "实验指标:\n" + "\n".join(metric_lines)

    # Run status
    context["run_success"] = run_result.get("success", False)
    context["run_returncode"] = run_result.get("returncode", -1)
    context["source_project_path"] = str(project_root)

    return context


def _extract_source_summary(project_root: Path, source_files: list[str]) -> dict[str, Any]:
    """Read source files and extract project summary, methods, and clues."""
    import tokenize, io

    all_code = []
    for rel in source_files:
        try:
            fpath = project_root / rel
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            all_code.append((rel, content))
        except Exception:
            pass

    if not all_code:
        return {}

    # Build a combined summary from all source files
    comments: list[str] = []
    functions: list[str] = []
    key_vars: list[str] = []
    class_names: list[str] = []

    for rel, code in all_code:
        ext = Path(rel).suffix.lower()

        if ext == ".m":
            # MATLAB: extract function names, comments, key variable names
            for line in code.split("\n"):
                line_stripped = line.strip()
                if line_stripped.startswith("%"):
                    comment = line_stripped.lstrip("%").strip()
                    if len(comment) > 5 and len(comment) < 200:
                        comments.append(comment)
                if line_stripped.startswith("function"):
                    # Extract function name: "function [out] = name(args)" → "name"
                    fn = re.sub(r'^function\s+(\[.*?\]\s*=\s*|\w+\s*=\s*)?', '', line_stripped)
                    fn = re.split(r'[\(\s]', fn)[0].strip()
                    if fn and fn not in ("end",):
                        functions.append(fn)
                # Key variable patterns
                m = re.match(r'^(\w+)\s*=\s*', line_stripped)
                if m and not line_stripped.startswith("%"):
                    var = m.group(1)
                    if var not in ("for", "if", "while", "switch", "case", "end", "else"):
                        key_vars.append(var)

        elif ext == ".py":
            for line in code.split("\n"):
                line_stripped = line.strip()
                if line_stripped.startswith("#") and len(line_stripped) > 5:
                    comments.append(line_stripped.lstrip("#").strip())
                if line_stripped.startswith("def "):
                    functions.append(line_stripped[:120])
                if line_stripped.startswith("class "):
                    class_names.append(line_stripped.split("(")[0].replace("class ", "").strip(":"))

    # Build summary
    summary_parts = []
    if functions:
        unique_funcs = list(dict.fromkeys(functions))[:10]
        summary_parts.append(f"项目包含 {len(functions)} 个函数/过程，核心包括：{'; '.join(unique_funcs[:8])}")
    if class_names:
        summary_parts.append(f"核心类：{', '.join(class_names[:5])}")
    if comments:
        # Pick most informative comments
        meaningful = [c for c in comments if any(kw in c.lower() for kw in
            ["路径", "规划", "覆盖", "障碍", "地图", "清扫", "机器人", "导航",
             "path", "plan", "cover", "obstacle", "map", "robot", "navig"])]
        if meaningful:
            summary_parts.append("核心逻辑：" + "; ".join(meaningful[:6]))

    summary = "。".join(summary_parts) if summary_parts else ""

    # Method clues: extract from function names and comments
    methods = []
    for f in functions[:10]:
        # Extract meaningful name
        name = re.sub(r'^(function|def)\s+', '', f)
        name = re.split(r'[\(=]', name)[0].strip()
        if name:
            methods.append(name)

    # Result clues from key variables and comments
    clues = []
    for c in comments[:10]:
        if any(kw in c for kw in ["结果", "参数", "设置", "实验", "结果", "配置", "精度", "分辨率", "半径"]):
            clues.append(c[:150])
    # Add key variable names as clues
    unique_vars = list(dict.fromkeys(key_vars))[:15]
    if unique_vars:
        clues.append(f"核心变量：{', '.join(unique_vars)}")

    # --- Extract key source code snippets for LLM context ---
    code_snippets = []
    for rel, code in all_code[:8]:
        ext = Path(rel).suffix.lower()
        if ext in (".m", ".py"):
            # Take first 30 non-empty, non-comment lines
            lines = []
            for line in code.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("%") and not stripped.startswith("#"):
                    lines.append(stripped)
                if len(lines) >= 30:
                    break
            if lines:
                code_snippets.append(f"--- {rel} ---\n" + "\n".join(lines[:30]))

    return {
        "summary": summary,
        "methods": methods[:8],
        "clues": clues[:8],
        "code_snippets": code_snippets[:5],
    }


def _detect_stack(project_root: Path) -> list[str]:
    """Detect technology stack from project files."""
    stack = []
    if list(project_root.glob("*.m")):
        stack.append("MATLAB")
    if list(project_root.glob("*.py")):
        stack.append("Python")
    if (project_root / "package.xml").exists():
        stack.append("ROS")
    if list(project_root.glob("*.cpp")) + list(project_root.glob("*.h")):
        stack.append("C++")
    if (project_root / "requirements.txt").exists():
        stack.append("pip")
    return stack or ["Unknown"]


def _extract_clues_from_output(stdout: str) -> list[str]:
    """Extract data clues from console output."""
    clues = []
    # Look for lines containing numbers, percentages, metrics
    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Match lines with key=value or key: value patterns
        if re.search(r"[\w_]+\s*[=:]\s*[\d.]+", line):
            clues.append(line[:200])
        # Match lines with percentage
        if "%" in line and re.search(r"\d+\.?\d*%", line):
            clues.append(line[:200])
    return clues[:20]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_project(
    project_root: str | Path,
    *,
    entry_script: str | None = None,
    timeout: int = 300,
    dry_run: bool = False,
    allow_agent_modifications: bool | None = None,
) -> dict[str, Any]:
    """Auto-detect, run a project, and collect data for paper generation.

    Args:
        project_root: Path to the project directory.
        entry_script: Optional override for the entry script. Auto-detected if None.
        timeout: Maximum execution time in seconds.
        dry_run: If True, only detect and scan without running.
        allow_agent_modifications: Explicit opt-in for agent auto_fix/auto_supplement
            source edits. Defaults to False unless
            SCIPILOT_AGENT_ALLOW_PROJECT_EDITS is enabled.

    Returns:
        Dict with keys:
            project_type: Detected type string
            entry_script: Path to the entry script used
            run_result: {success, stdout, stderr, returncode}
            collected: {figures, csv_files, json_files, metrics}
            project_context: Dict compatible with generate_paper_package()
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    # Step 1: Detect project type
    project_type = detect_project_type(root)
    print(f"[ProjectRunner] 类型: {project_type}")

    # Step 2: Find entry script
    if entry_script is None:
        entry_script = find_entry_script(root, project_type)
    if entry_script:
        print(f"[ProjectRunner] 入口: {entry_script}")
    else:
        print(f"[ProjectRunner] 未找到入口脚本，跳过运行")

    # Step 3: Run project (if entry script found and not dry_run)
    run_result: dict[str, Any] = {"success": False, "stdout": "", "stderr": "No entry script", "returncode": -1}
    if entry_script and not dry_run:
        print(f"[ProjectRunner] 开始运行 (timeout={timeout}s)...")
        t0 = time.time()
        if project_type == "matlab":
            run_result = _run_matlab(root, entry_script, timeout=timeout)
        elif project_type == "python":
            run_result = _run_python(root, entry_script, timeout=timeout)
        else:
            run_result = _run_generic(root, entry_script, timeout=timeout)
        elapsed = time.time() - t0
        print(f"[ProjectRunner] 运行完成: success={run_result['success']} time={elapsed:.1f}s")
        if run_result.get("stderr"):
            print(f"[ProjectRunner] stderr: {run_result['stderr'][:300]}")

    # Step 4: Collect output data
    collected = _collect_output_data(root)
    print(f"[ProjectRunner] 采集: {len(collected['figures'])} 张图, {len(collected['csv_files'])} 个CSV, {len(collected['metrics'])} 个指标")

    # Step 4.5: Agent-assisted fix + supplement (if enabled)
    agent_modifications_allowed = _allow_agent_project_edits(allow_agent_modifications)
    try:
        from tools.agent_bridge import (
            agent_enabled, run_agent_task, results_sufficient,
            build_fix_prompt, build_supplement_prompt, _agent_config,
        )
        _agent_cfg = _agent_config()
        _agent_enabled = agent_enabled()

        if _agent_enabled and not dry_run and not agent_modifications_allowed:
            if _agent_cfg.get("auto_fix") or _agent_cfg.get("auto_supplement"):
                print(
                    "[ProjectRunner] Agent auto-edit is disabled by default; "
                    "pass allow_agent_modifications=True or set "
                    "SCIPILOT_AGENT_ALLOW_PROJECT_EDITS=1 to opt in."
                )

        if _agent_enabled and not dry_run and agent_modifications_allowed:
            # Auto-fix on failure
            if not run_result["success"] and _agent_cfg.get("auto_fix"):
                print(f"[ProjectRunner] 运行失败，调用 AI 代理修复...")
                fix_result = run_agent_task(
                    task=build_fix_prompt(run_result, project_type, entry_script),
                    project_path=str(root),
                    agent_config=_agent_cfg,
                )
                if fix_result["success"]:
                    print(f"[ProjectRunner] 代理修复成功，重新运行项目 (修改了 {len(fix_result['files_modified'])} 个文件)")
                    if entry_script and project_type == "python":
                        run_result = _run_python(root, entry_script, timeout=timeout)
                    elif entry_script:
                        run_result = _run_generic(root, entry_script, timeout=timeout)
                    collected = _collect_output_data(root)
                    print(f"[ProjectRunner] 重新采集: {len(collected['figures'])} 张图, {len(collected['csv_files'])} 个CSV")
                else:
                    print(f"[ProjectRunner] 代理修复失败: {fix_result.get('error', 'unknown')}")

            # Auto-supplement when results insufficient
            if run_result["success"] and not results_sufficient(collected) and _agent_cfg.get("auto_supplement"):
                print(f"[ProjectRunner] 结果不充分，调用 AI 代理补充实验数据/图表...")
                supplement_result = run_agent_task(
                    task=build_supplement_prompt(collected, project_type),
                    project_path=str(root),
                    agent_config=_agent_cfg,
                )
                if supplement_result["success"]:
                    print(f"[ProjectRunner] 代理补充成功，重新运行项目 (修改了 {len(supplement_result['files_modified'])} 个文件)")
                    if entry_script and project_type == "python":
                        run_result = _run_python(root, entry_script, timeout=timeout)
                    elif entry_script:
                        run_result = _run_generic(root, entry_script, timeout=timeout)
                    collected = _collect_output_data(root)
                    print(f"[ProjectRunner] 重新采集: {len(collected['figures'])} 张图, {len(collected['csv_files'])} 个CSV")
                else:
                    print(f"[ProjectRunner] 代理补充失败: {supplement_result.get('error', 'unknown')}")
    except ImportError:
        pass  # agent_bridge not available, skip
    except Exception as exc:
        print(f"[ProjectRunner] Agent 集成异常（继续）: {exc}")

    # Step 5: Build project_context
    project_context = _build_project_context(root, run_result, collected)

    return {
        "project_type": project_type,
        "entry_script": entry_script,
        "run_result": run_result,
        "collected": collected,
        "project_context": project_context,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run a project and collect data")
    parser.add_argument("project_root", help="Path to the project directory")
    parser.add_argument("--entry", help="Override entry script path")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Only detect, don't run")
    parser.add_argument(
        "--allow-agent-modifications",
        action="store_true",
        help=(
            "Allow agent auto_fix/auto_supplement to modify source files. "
            "Disabled by default; or set SCIPILOT_AGENT_ALLOW_PROJECT_EDITS=1."
        ),
    )
    parser.add_argument("--output-json", help="Save results to JSON file")
    args = parser.parse_args()

    result = run_project(
        args.project_root,
        entry_script=args.entry,
        timeout=args.timeout,
        dry_run=args.dry_run,
        allow_agent_modifications=args.allow_agent_modifications,
    )

    # Print summary
    print(f"\n=== 项目运行结果 ===")
    print(f"类型: {result['project_type']}")
    print(f"入口: {result['entry_script']}")
    print(f"成功: {result['run_result']['success']}")
    print(f"图片: {len(result['collected']['figures'])}")
    print(f"CSV:  {len(result['collected']['csv_files'])}")
    print(f"指标: {len(result['collected']['metrics'])}")

    if args.output_json:
        # Make serializable
        serializable = {
            "project_type": result["project_type"],
            "entry_script": result["entry_script"],
            "run_success": result["run_result"]["success"],
            "stdout": result["run_result"]["stdout"][:2000],
            "stderr": result["run_result"]["stderr"][:1000],
            "figures": [f["relative"] for f in result["collected"]["figures"]],
            "csv_files": [f["name"] for f in result["collected"]["csv_files"]],
            "metrics": result["collected"]["metrics"],
        }
        Path(args.output_json).write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已保存: {args.output_json}")
