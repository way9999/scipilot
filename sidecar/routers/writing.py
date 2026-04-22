"""Writing and research-generation endpoints for the SciPilot standalone app."""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from tools.text_safety import safe_json_dumps, sanitize_utf8_text

router = APIRouter(prefix="/writing", tags=["writing"])


def _project_root() -> Path:
    env_root = os.environ.get("SCIPILOT_USER_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _project_root()


class PaperDraftRequest(BaseModel):
    topic: str = Field(default="")
    language: str = Field(default="auto")
    paper_type: str = Field(default="general")
    target_words: int | None = None
    reference_files: list[str] | None = None


class ProjectPaperRequest(PaperDraftRequest):
    source_project: str = Field(..., min_length=1)


class TopicRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    language: str = Field(default="auto")


class PresentationRequest(TopicRequest):
    deck_type: str = Field(default="proposal_review")
    target_audience: str = Field(default="")
    style: str = Field(default="")
    page_count: int = Field(default=0)


class RefinementRequest(BaseModel):
    source: str = Field(..., min_length=1)
    language: str = Field(default="auto")


class ResearchQuestionRequest(BaseModel):
    question: str = Field(..., min_length=1)
    language: str = Field(default="auto")


class ExportDocxRequest(BaseModel):
    artifact: str = Field(default="paper")
    source: str | None = None
    output: str | None = None
    topic: str | None = None
    question: str | None = None
    language: str = Field(default="auto")
    paper_type: str = Field(default="general")
    target_words: int | None = None
    docx_style: str = Field(default="default")
    deck_type: str = Field(default="proposal_review")


class ExportPptxRequest(BaseModel):
    source: str | None = None
    output: str | None = None
    topic: str | None = None
    language: str = Field(default="auto")
    deck_type: str = Field(default="proposal_review")
    widescreen: bool = Field(default=True)


def _to_relative(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _unique_paths(*groups: list[str | None]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for item in group:
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
    return ordered


MAX_REFERENCE_FILES = 20
MAX_REFERENCE_FILE_CHARS = 12000
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".svg", ".gif", ".tiff", ".tif"}
MAX_PROGRESS_EVENTS = 12
PROGRESS_WRITE_LOCK = threading.Lock()

PROGRESS_PHASE_LABELS = {
    "boot": "任务启动",
    "scan": "项目分析",
    "run": "项目运行",
    "collect": "结果采集",
    "outline": "结构规划",
    "drafting": "章节起草",
    "expanding": "章节扩写",
    "enhance": "正文增强",
    "finalize": "终稿整理",
    "figures": "图表处理",
    "citations": "引用补全",
    "polish": "终稿润色",
    "quality": "质量检查",
    "export": "结果写出",
}


def _extract_reference_file_content(file_paths: list[str]) -> list[dict[str, Any]]:
    """Extract text content from user-provided reference files (max 20)."""
    results: list[dict[str, Any]] = []
    for file_path in file_paths[:MAX_REFERENCE_FILES]:
        path = Path(file_path)
        if not path.exists():
            results.append({"path": file_path, "filename": path.name, "error": f"File not found: {file_path}"})
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf":
                from pypdf import PdfReader
                reader = PdfReader(str(path))
                text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
                results.append({"path": file_path, "filename": path.name, "content": text[:MAX_REFERENCE_FILE_CHARS], "pages": len(reader.pages)})
            elif suffix in (".txt", ".md", ".markdown"):
                text = path.read_text(encoding="utf-8", errors="ignore")
                results.append({"path": file_path, "filename": path.name, "content": text[:MAX_REFERENCE_FILE_CHARS]})
            elif suffix == ".docx":
                try:
                    import docx
                    doc = docx.Document(str(path))
                    text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
                    results.append({"path": file_path, "filename": path.name, "content": text[:MAX_REFERENCE_FILE_CHARS]})
                except ImportError:
                    results.append({"path": file_path, "filename": path.name, "error": "python-docx not installed"})
            elif suffix in IMAGE_EXTENSIONS:
                results.append({"path": file_path, "filename": path.name, "content": "", "is_image": True,
                                "description": f"[Image file: {path.name}, can be used as a figure in the paper]"})
            else:
                text = path.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    results.append({"path": file_path, "filename": path.name, "content": text[:MAX_REFERENCE_FILE_CHARS]})
                else:
                    results.append({"path": file_path, "filename": path.name, "error": f"Unsupported file type: {suffix}"})
        except Exception as exc:
            results.append({"path": file_path, "filename": path.name, "error": str(exc)})
    if len(file_paths) > MAX_REFERENCE_FILES:
        results.append({"path": "", "filename": "", "error": f"Only first {MAX_REFERENCE_FILES} files processed, {len(file_paths) - MAX_REFERENCE_FILES} skipped"})
    return results


def _normalize_result(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    artifact = result.get("artifact") if isinstance(result.get("artifact"), dict) else {}
    supporting_assets = artifact.get("supporting_assets") if isinstance(artifact.get("supporting_assets"), dict) else {}
    quality_meta = artifact.get("quality_meta") if isinstance(artifact.get("quality_meta"), dict) else {}
    if not quality_meta and isinstance(result.get("quality_meta"), dict):
        quality_meta = result.get("quality_meta")

    target_words = artifact.get("target_words")
    if target_words is None:
        target_words = result.get("target_words")

    actual_words = artifact.get("actual_words")
    if actual_words is None:
        actual_words = result.get("actual_words")

    markdown_path = _to_relative(result.get("markdown_path"))
    outline_path = _to_relative(result.get("outline_path"))
    plan_path = _to_relative(result.get("plan_path"))
    prompts_path = _to_relative(result.get("prompts_path"))
    latex_path = _to_relative(result.get("latex_path"))
    bib_path = _to_relative(result.get("bib_path"))
    json_path = _to_relative(result.get("json_path"))
    html_path = _to_relative(result.get("html_path"))
    output_path = _to_relative(result.get("output_path"))
    project_analysis_path = _to_relative(result.get("project_analysis_path"))

    asset_paths = [_to_relative(str(value)) for value in supporting_assets.values()]
    artifact_paths = _unique_paths(
        [
            markdown_path,
            outline_path,
            plan_path,
            prompts_path,
            latex_path,
            bib_path,
            json_path,
            html_path,
            output_path,
            project_analysis_path,
        ],
        asset_paths,
    )

    title = (
        artifact.get("title")
        or result.get("title")
        or f"{kind.replace('_', ' ').title()} Result"
    )
    summary = (
        artifact.get("summary")
        or result.get("summary")
        or output_path
        or markdown_path
        or title
    )

    return {
        "kind": kind,
        "markdown_path": markdown_path,
        "outline_path": outline_path,
        "plan_path": plan_path,
        "prompts_path": prompts_path,
        "latex_path": latex_path,
        "bib_path": bib_path,
        "json_path": json_path,
        "html_path": html_path,
        "output_path": output_path,
        "project_analysis_path": project_analysis_path,
        "artifact_paths": artifact_paths,
        "primary_path": artifact_paths[0] if artifact_paths else None,
        "artifact": {
            "title": title,
            "summary": summary,
            "language": artifact.get("language") or result.get("language"),
            "paper_type": artifact.get("paper_type") or result.get("paper_type"),
            "target_words": target_words,
            "actual_words": actual_words,
            "quality_meta": quality_meta or None,
            "quality_report": result.get("quality_report"),
        },
        "state": result.get("state"),
    }


def _progress_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _progress_phase_label(phase: str | None) -> str | None:
    if not phase:
        return None
    return PROGRESS_PHASE_LABELS.get(phase, phase)


def _looks_mojibake(text: str | None) -> bool:
    if not text:
        return False
    markers = "姝鍦绗缁璁鐢浠鍚杩鎵琛鏂鍒妫€"
    return sum(text.count(ch) for ch in markers) >= 2


def _report_step(step: int, label: str, detail: str = "") -> None:
    """Report progress step to the worker entry point (no-op if not in worker)."""
    try:
        import sidecar.routers.writing as _self
        _self._current_step(step, label, detail)  # type: ignore[attr-defined]
    except Exception:
        pass


def _section_progress_callback(section_idx: int, section_total: int, section_title: str, phase: str) -> None:
    """Callback for writing_enhancer to report per-section progress."""
    try:
        import sidecar.routers.writing as _self
        _self._current_section_progress(section_idx, section_total, section_title, phase)  # type: ignore[attr-defined]
    except Exception:
        pass


def _friendly_error(exc: Exception, step_label: str) -> str:
    """Map common exceptions to user-friendly error messages."""
    msg = str(exc).lower()
    if "connection" in msg or "timeout" in msg or "timed out" in msg:
        return f"网络连接失败 ({step_label})：请检查 API 地址是否正确、网络是否通畅。"
    if "401" in msg or "unauthorized" in msg or "authentication" in msg or "api_key" in msg or "apikey" in msg:
        return f"API 密钥无效 ({step_label})：请到设置页面检查 API Key 是否正确。"
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return f"API 调用频率超限 ({step_label})：请稍后重试，或更换 API 提供商。"
    if "500" in msg or "502" in msg or "503" in msg or "internal server error" in msg or "server error" in msg:
        return f"API 服务端异常 ({step_label})：LLM 服务暂时不可用，请稍后重试。"
    if "no llm config" in msg or "llm_config" in msg or "no api" in msg:
        return f"未配置 LLM ({step_label})：请到设置页面配置 API 地址和密钥。"
    if "file not found" in msg or "no such file" in msg:
        return f"文件未找到 ({step_label})：{exc}"
    if "import" in msg or "module" in msg or "modulenotfound" in msg:
        return f"依赖缺失 ({step_label})：{exc}"
    return f"{step_label}失败：{exc}"


def _generate_topic_paper_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.paper_writer import generate_paper_package

    _report_step(1, "正在分析主题...")
    project_context = None
    ref_files = payload.get("reference_files")
    if ref_files:
        try:
            extracted = _extract_reference_file_content(ref_files)
            project_context = {"uploaded_references": extracted}
        except Exception as exc:
            _report_step(1, "正在分析主题...", f"参考文件读取失败: {exc}")

    _report_step(2, "正在生成论文大纲...")
    try:
        _report_step(3, "正在调用 LLM 撰写各章节...")
        result = generate_paper_package(
            PROJECT_ROOT,
            payload["topic"].strip(),
            payload["language"],
            payload["paper_type"],
            project_context,
            payload.get("target_words"),
            progress_callback=_report_step,
        )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, "LLM 论文生成")) from exc

    _report_step(4, "正在后处理与格式化...")
    return _normalize_result("paper", result)


def _generate_project_paper_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.paper_writer import generate_paper_package
    from tools.project_paper_context import analyze_project_for_paper

    _report_step(1, "正在扫描项目结构...")
    try:
        project_context = analyze_project_for_paper(
            PROJECT_ROOT,
            payload["source_project"],
            payload["topic"].strip(),
        )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, "项目分析")) from exc

    ref_files = payload.get("reference_files")
    if ref_files:
        try:
            extracted = _extract_reference_file_content(ref_files)
            project_context["uploaded_references"] = extracted
        except Exception as exc:
            _report_step(1, "正在扫描项目结构...", f"参考文件读取失败: {exc}")

    _report_step(2, "正在运行项目代码...")
    _report_step(3, "正在采集实验结果...")
    _report_step(4, "正在生成论文大纲...")
    try:
        _report_step(5, "正在调用 LLM 撰写各章节...")
        result = generate_paper_package(
            PROJECT_ROOT,
            payload["topic"].strip(),
            payload["language"],
            payload["paper_type"],
            project_context,
            payload.get("target_words"),
            progress_callback=_report_step,
        )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, "LLM 论文生成")) from exc

    _report_step(6, "正在后处理与格式化...")
    result["project_analysis_path"] = project_context.get("analysis_path")
    return _normalize_result("project_paper", result)


def _generate_proposal_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.research_bridge import _generate_proposal

    _report_step(1, "正在检索相关文献...")
    _report_step(2, "正在调用 LLM 生成开题报告...")
    try:
        result = _generate_proposal(
            argparse.Namespace(
                project_root=str(PROJECT_ROOT),
                topic=payload["topic"].strip(),
                language=payload["language"],
            )
        )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, "LLM 开题报告生成")) from exc
    return _normalize_result("proposal", result)


def _generate_presentation_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.research_bridge import _generate_presentation

    _report_step(1, "正在调用 LLM 生成汇报内容...")
    _report_step(2, "正在构建幻灯片...")
    try:
        result = _generate_presentation(
            argparse.Namespace(
                project_root=str(PROJECT_ROOT),
                topic=payload["topic"].strip(),
                language=payload["language"],
                deck_type=payload["deck_type"],
            )
        )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, "LLM 汇报生成")) from exc
    return _normalize_result("presentation", result)


def _generate_literature_review_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.literature_review import generate_literature_review

    _report_step(1, "正在检索相关文献...")
    _report_step(2, "正在调用 LLM 撰写文献综述...")
    try:
        result = generate_literature_review(
            topic=payload["topic"].strip(),
            project_root=PROJECT_ROOT,
            language=payload["language"],
        )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, "LLM 文献综述生成")) from exc
    return _normalize_result("literature_review", result)


def _refine_draft_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.writing_refiner import refine_document_package

    _report_step(1, "正在加载草稿...")
    _report_step(2, "正在精修文档...")
    result = refine_document_package(
        project_root=PROJECT_ROOT,
        source=payload["source"].strip(),
        language=payload.get("language", "auto"),
    )
    return _normalize_result("refinement", result)


def _answer_research_question_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.research_qa import answer_research_question

    _report_step(1, "正在检索相关资料...")
    _report_step(2, "正在生成回答...")
    result = answer_research_question(
        question=payload["question"].strip(),
        project_root=PROJECT_ROOT,
        language=payload["language"],
    )
    return _normalize_result("research_qa", result)


def _export_docx_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.research_bridge import _export_docx

    _report_step(1, "正在准备文档...")
    _report_step(2, "正在生成 DOCX...")

    result = _export_docx(
        argparse.Namespace(
            project_root=str(PROJECT_ROOT),
            artifact=payload["artifact"],
            source=payload.get("source"),
            output=payload.get("output"),
            topic=payload.get("topic"),
            question=payload.get("question"),
            language=payload.get("language", "auto"),
            paper_type=payload.get("paper_type", "general"),
            target_words=payload.get("target_words"),
            docx_style=payload.get("docx_style", "default"),
            deck_type=payload.get("deck_type", "proposal_review") if payload.get("artifact") == "presentation" else "proposal_review",
        )
    )
    return _normalize_result("export_docx", result["artifact"])


def _export_pptx_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.research_bridge import _export_pptx

    _report_step(1, "正在准备幻灯片...")
    _report_step(2, "正在生成 PPTX...")

    result = _export_pptx(
        argparse.Namespace(
            project_root=str(PROJECT_ROOT),
            source=payload.get("source"),
            output=payload.get("output"),
            topic=payload.get("topic"),
            language=payload.get("language", "auto"),
            deck_type=payload.get("deck_type", "proposal_review"),
        )
    )
    return _normalize_result("export_pptx", result["artifact"])


def _generate_topic_paper_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.paper_writer import generate_paper_package

    _report_step(1, "正在分析论文主题...", "检查主题、语言、目标字数和参考文件")
    project_context = None
    ref_files = payload.get("reference_files")
    if ref_files:
        try:
            extracted = _extract_reference_file_content(ref_files)
            project_context = {"uploaded_references": extracted}
        except Exception as exc:
            _report_step(1, "正在分析论文主题...", f"参考文件读取失败：{exc}")

    try:
        result = generate_paper_package(
            PROJECT_ROOT,
            payload["topic"].strip(),
            payload["language"],
            payload["paper_type"],
            project_context,
            payload.get("target_words"),
            progress_callback=_report_step,
        )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, "LLM 论文生成")) from exc

    _report_step(4, "正在整理终稿与附件...", "正在归档草稿、提纲、质量信息和导出文件")
    return _normalize_result("paper", result)


def _generate_project_paper_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.paper_writer import generate_paper_package
    from tools.project_paper_context import analyze_project_for_paper

    _report_step(1, "正在扫描项目结构...", "读取 README、源码、配置、日志和历史结果文件")
    try:
        project_context = analyze_project_for_paper(
            PROJECT_ROOT,
            payload["source_project"],
            payload["topic"].strip(),
        )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, "项目分析")) from exc

    ref_files = payload.get("reference_files")
    if ref_files:
        try:
            extracted = _extract_reference_file_content(ref_files)
            project_context["uploaded_references"] = extracted
        except Exception as exc:
            _report_step(1, "正在扫描项目结构...", f"参考文件读取失败：{exc}")

    try:
        result = generate_paper_package(
            PROJECT_ROOT,
            payload["topic"].strip(),
            payload["language"],
            payload["paper_type"],
            project_context,
            payload.get("target_words"),
            progress_callback=_report_step,
        )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, "LLM 论文生成")) from exc

    _report_step(6, "正在整理终稿与附件...", "正在归档项目分析、草稿、图表和质量报告")
    result["project_analysis_path"] = project_context.get("analysis_path")
    return _normalize_result("project_paper", result)


WORKER_HANDLERS = {
    "paper": _generate_topic_paper_sync,
    "project": _generate_project_paper_sync,
    "proposal": _generate_proposal_sync,
    "presentation": _generate_presentation_sync,
    "literature_review": _generate_literature_review_sync,
    "refinement": _refine_draft_sync,
    "research_qa": _answer_research_question_sync,
    "export_docx": _export_docx_sync,
    "export_pptx": _export_pptx_sync,
}


def _progress_steps_for_mode(mode: str) -> list[str]:
    steps = TASK_PROGRESS_STEPS.get(mode)
    return list(steps) if steps else ["Processing..."]


def _worker_entry(mode: str, payload: dict[str, Any], result_path: str) -> None:
    # Ensure tools/ and scipilot/ are importable in the spawned subprocess.
    import sys as _sys
    _resource_root = os.environ.get("SCIPILOT_RESOURCE_ROOT", "").strip()
    if _resource_root:
        _root = Path(_resource_root).resolve()
    else:
        _root = Path(__file__).resolve().parents[3]
    for _candidate in [str(_root), str(_root / "scipilot")]:
        if _candidate not in _sys.path:
            _sys.path.insert(0, _candidate)

    result_file = Path(result_path)
    progress_file = result_file.parent / "progress.json"

    # Monkey-patch a progress writer so handlers can report steps
    import sidecar.routers.writing as _self
    _current_result_path = result_path
    steps = _progress_steps_for_mode(mode)
    _current_step_idx = 0
    _current_step_label = steps[0] if steps else "Starting..."
    _step_phases = PROGRESS_STEP_PHASES.get(mode, {})

    def _step(step: int, label: str, detail: str = "") -> None:
        nonlocal _current_step_idx, _current_step_label
        total = len(steps)
        current_step = max(0, min(step, total))
        _current_step_idx = max(current_step - 1, 0) if current_step else 0
        if phase == "drafting":
            label = f"正在起草第 {section_idx + 1}/{section_total} 章：{section_title}"
        elif phase == "expanding":
            label = f"正在扩写第 {section_idx + 1}/{section_total} 章：{section_title}"
        else:
            label = f"正在处理第 {section_idx + 1}/{section_total} 章：{section_title}"
        _current_step_label = label
        _write_progress(
            _current_result_path,
            current_step,
            total,
            label,
            detail,
            phase=_step_phases.get(current_step),
        )

    def _section_progress(section_idx: int, section_total: int, section_title: str, phase: str) -> None:
        """Update progress with sub-step detail for section writing."""
        nonlocal _current_step_label
        current_step = _current_step_idx + 1 if steps else 0
        label = f"正在撰写第 {section_idx + 1}/{section_total} 章: {section_title}"
        if phase == "expanding":
            label = f"正在扩写第 {section_idx + 1}/{section_total} 章: {section_title}"
        _current_step_label = label
        detail = "正在根据章节蓝图生成正文" if phase == "drafting" else "正在结合证据包补写章节"
        _write_progress(
            _current_result_path,
            current_step,
            len(steps),
            label,
            detail,
            phase=phase,
        )

    _self._current_step = _step  # type: ignore[attr-defined]
    _self._current_section_progress = _section_progress  # type: ignore[attr-defined]

    # Write initial progress
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    _write_progress(
        _current_result_path,
        0,
        len(steps),
        _current_step_label,
        "任务已创建，正在准备工作进程",
        phase="boot",
    )

    try:
        handler = WORKER_HANDLERS[mode]
        result = handler(payload)
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(
            safe_json_dumps({"success": True, "result": result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # pragma: no cover
        import traceback as _tb
        _tb.print_exc()
        _step_label = _current_step_label
        _write_progress(
            _current_result_path,
            _current_step_idx + 1 if steps else 0,
            len(steps),
            _step_label,
            sanitize_utf8_text(f"任务失败：{exc}"),
            phase=_step_phases.get(_current_step_idx + 1),
        )
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(
            safe_json_dumps({"success": False, "error": _friendly_error(exc, _step_label)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _worker_entry(mode: str, payload: dict[str, Any], result_path: str) -> None:
    # Ensure tools/ and scipilot/ are importable in the spawned subprocess.
    import sys as _sys

    _resource_root = os.environ.get("SCIPILOT_RESOURCE_ROOT", "").strip()
    if _resource_root:
        _root = Path(_resource_root).resolve()
    else:
        _root = Path(__file__).resolve().parents[3]
    for _candidate in [str(_root), str(_root / "scipilot")]:
        if _candidate not in _sys.path:
            _sys.path.insert(0, _candidate)

    result_file = Path(result_path)
    progress_file = result_file.parent / "progress.json"

    import sidecar.routers.writing as _self

    _current_result_path = result_path
    steps = _progress_steps_for_mode(mode)
    _current_step_idx = 0
    _current_step_label = steps[0] if steps else "Starting..."
    _step_phases = PROGRESS_STEP_PHASES.get(mode, {})

    def _step(step: int, label: str, detail: str = "") -> None:
        nonlocal _current_step_idx, _current_step_label
        total = len(steps)
        current_step = max(0, min(step, total))
        _current_step_idx = max(current_step - 1, 0) if current_step else 0
        _current_step_label = label
        _write_progress(
            _current_result_path,
            current_step,
            total,
            label,
            detail,
            phase=_step_phases.get(current_step),
        )

    def _section_progress(section_idx: int, section_total: int, section_title: str, phase: str) -> None:
        nonlocal _current_step_label
        current_step = _current_step_idx + 1 if steps else 0
        if phase == "drafting":
            label = f"正在起草第 {section_idx + 1}/{section_total} 章：{section_title}"
            detail = "正在根据章节蓝图生成正文"
        elif phase == "expanding":
            label = f"正在扩写第 {section_idx + 1}/{section_total} 章：{section_title}"
            detail = "正在结合证据包补写章节"
        else:
            label = f"正在处理第 {section_idx + 1}/{section_total} 章：{section_title}"
            detail = "正在更新章节内容"
        _current_step_label = label
        _write_progress(
            _current_result_path,
            current_step,
            len(steps),
            label,
            detail,
            phase=phase,
        )

    _self._current_step = _step  # type: ignore[attr-defined]
    _self._current_section_progress = _section_progress  # type: ignore[attr-defined]

    progress_file.parent.mkdir(parents=True, exist_ok=True)
    _write_progress(
        _current_result_path,
        0,
        len(steps),
        _current_step_label,
        "任务已创建，正在准备工作进程",
        phase="boot",
    )

    try:
        handler = WORKER_HANDLERS[mode]
        result = handler(payload)
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(
            safe_json_dumps({"success": True, "result": result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # pragma: no cover
        import traceback as _tb

        _tb.print_exc()
        _step_label = _current_step_label
        _write_progress(
            _current_result_path,
            _current_step_idx + 1 if steps else 0,
            len(steps),
            _step_label,
            sanitize_utf8_text(f"任务失败：{exc}"),
            phase=_step_phases.get(_current_step_idx + 1),
        )
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(
            safe_json_dumps({"success": False, "error": _friendly_error(exc, _step_label)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class WritingTaskManager:
    def __init__(self) -> None:
        self._ctx = mp.get_context("spawn")
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}

    def start(self, mode: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            running_tasks = self._running_tasks_locked()
            task_id = uuid.uuid4().hex
            task_dir = PROJECT_ROOT / "output" / "writing-tasks" / task_id
            result_path = task_dir / "result.json"
            if result_path.exists():
                result_path.unlink()
            process = self._ctx.Process(
                target=_worker_entry,
                args=(mode, payload, str(result_path)),
                daemon=True,
            )
            process.start()
            progress_steps = _progress_steps_for_mode(mode)
            self._tasks[task_id] = {
                "id": task_id,
                "mode": mode,
                "status": "running",
                "process": process,
                "result_path": result_path,
                "result": None,
                "error": None,
                "progress": {
                    "step": 0,
                    "total": len(progress_steps),
                    "label": progress_steps[0],
                    "detail": "任务已创建，等待工作进程启动",
                    "events": [],
                },
                "replaced_by_task_id": None,
            }
            for task in running_tasks:
                self._cancel_task_locked(task, f"Task canceled because it was replaced by task {task_id}.", replaced_by_task_id=task_id)
            return {"task_id": task_id, "status": "running"}

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            self._refresh_task_locked(task)
            return self._serialize(task)

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task["status"] == "running":
                self._cancel_task_locked(task, "Task canceled by user.")
            return self._serialize(task)

    def _running_tasks_locked(self) -> list[dict[str, Any]]:
        running_tasks: list[dict[str, Any]] = []
        for task in self._tasks.values():
            if task["status"] != "running":
                continue
            self._refresh_task_locked(task)
            if task["status"] == "running":
                running_tasks.append(task)
        return running_tasks

    def _cancel_task_locked(
        self,
        task: dict[str, Any],
        reason: str,
        *,
        replaced_by_task_id: str | None = None,
    ) -> None:
        process = task["process"]
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                try:
                    process.kill()
                    process.join(timeout=1)
                except Exception:
                    pass
        task["status"] = "canceled"
        task["error"] = reason
        task["replaced_by_task_id"] = replaced_by_task_id

    def _refresh_task_locked(self, task: dict[str, Any]) -> None:
        if task["status"] != "running":
            return

        result_path: Path = task["result_path"]
        message = None
        if result_path.exists():
            try:
                message = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                message = {"success": False, "error": "Task result file is corrupted."}

        if message is not None:
            if message.get("success"):
                task["status"] = "completed"
                task["result"] = message.get("result")
                task["error"] = None
            else:
                task["status"] = "failed"
                task["error"] = message.get("error") or "Task failed."
            process = task["process"]
            process.join(timeout=1)
            return

        process = task["process"]
        if not process.is_alive():
            process.join(timeout=1)
            task["status"] = "failed"
            exit_code = process.exitcode
            if task["error"]:
                return
            if exit_code is None:
                task["error"] = "Task exited unexpectedly."
            else:
                task["error"] = f"Task exited unexpectedly (exit code {exit_code})."

    def _serialize(self, task: dict[str, Any]) -> dict[str, Any]:
        # Read progress from progress.json if available
        progress = task.get("progress") or {"step": 0, "total": 1, "label": ""}
        try:
            progress_file = task["result_path"].parent / "progress.json"
            if progress_file.exists():
                progress = json.loads(progress_file.read_text(encoding="utf-8"))
        except Exception:
            pass

        return {
            "task_id": task["id"],
            "status": task["status"],
            "result": task.get("result"),
            "error": task.get("error"),
            "progress": progress,
            "replaced_by_task_id": task.get("replaced_by_task_id"),
        }


TASK_MANAGER = WritingTaskManager()

PROGRESS_STEPS = {
    "paper": [
        "正在分析主题...",
        "正在生成论文大纲...",
        "正在调用 LLM 撰写各章节...",
        "正在后处理与格式化...",
    ],
    "project": [
        "正在扫描项目结构...",
        "正在运行项目代码...",
        "正在采集实验结果...",
        "正在生成论文大纲...",
        "正在调用 LLM 撰写各章节...",
        "正在后处理与格式化...",
    ],
    "proposal": [
        "正在检索相关文献...",
        "正在调用 LLM 生成开题报告...",
        "正在后处理...",
    ],
    "literature_review": [
        "正在检索相关文献...",
        "正在调用 LLM 撰写文献综述...",
        "正在后处理...",
    ],
    "review": [
        "正在检索相关文献...",
        "正在调用 LLM 撰写文献综述...",
        "正在后处理...",
    ],
    "refinement": [
        "正在加载草稿...",
        "正在精修文档...",
        "正在后处理...",
    ],
    "research_qa": [
        "正在检索相关资料...",
        "正在生成回答...",
    ],
    "qa": [
        "正在检索相关资料...",
        "正在生成回答...",
    ],
    "presentation": [
        "正在调用 LLM 生成汇报内容...",
        "正在构建幻灯片...",
    ],
    "export_docx": [
        "正在准备文档...",
        "正在生成 DOCX...",
    ],
    "export_pptx": [
        "正在准备幻灯片...",
        "正在生成 PPTX...",
    ],
}


def _write_progress(result_path: str, step: int, total: int, label: str, detail: str = "") -> None:
    """Write progress info to progress.json next to result.json."""
    try:
        p = Path(result_path).parent / "progress.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            safe_json_dumps(
                {
                    "step": step,
                    "total": total,
                    "label": sanitize_utf8_text(label),
                    "detail": sanitize_utf8_text(detail),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


TASK_PROGRESS_STEPS = {
    "paper": [
        "正在分析论文主题...",
        "正在规划论文结构...",
        "正在撰写论文正文...",
        "正在整理终稿与附件...",
    ],
    "project": [
        "正在扫描项目结构...",
        "正在运行项目代码...",
        "正在采集实验结果...",
        "正在规划论文结构...",
        "正在撰写论文正文...",
        "正在整理终稿与附件...",
    ],
    "proposal": [
        "正在检索相关文献...",
        "正在生成开题报告...",
        "正在整理输出结果...",
    ],
    "literature_review": [
        "正在检索相关文献...",
        "正在撰写文献综述...",
        "正在整理输出结果...",
    ],
    "review": [
        "正在检索相关文献...",
        "正在撰写文献综述...",
        "正在整理输出结果...",
    ],
    "refinement": [
        "正在加载草稿...",
        "正在精修正文...",
        "正在整理输出结果...",
    ],
    "research_qa": [
        "正在检索相关资料...",
        "正在生成问答结果...",
    ],
    "qa": [
        "正在检索相关资料...",
        "正在生成问答结果...",
    ],
    "presentation": [
        "正在生成演示大纲...",
        "正在生成幻灯片内容...",
        "正在整理演示文稿...",
    ],
    "export_docx": [
        "正在准备导出文档...",
        "正在生成 DOCX 文件...",
    ],
    "export_pptx": [
        "正在准备演示素材...",
        "正在生成 PPTX 文件...",
    ],
}

PROGRESS_STEP_PHASES = {
    "paper": {1: "scan", 2: "outline", 3: "enhance", 4: "finalize"},
    "project": {1: "scan", 2: "run", 3: "collect", 4: "outline", 5: "enhance", 6: "finalize"},
    "proposal": {1: "scan", 2: "drafting", 3: "finalize"},
    "literature_review": {1: "scan", 2: "drafting", 3: "finalize"},
    "review": {1: "scan", 2: "drafting", 3: "finalize"},
    "refinement": {1: "scan", 2: "polish", 3: "finalize"},
    "research_qa": {1: "scan", 2: "drafting"},
    "qa": {1: "scan", 2: "drafting"},
    "presentation": {1: "outline", 2: "drafting", 3: "finalize"},
    "export_docx": {1: "export", 2: "export"},
    "export_pptx": {1: "export", 2: "export"},
}


def _write_progress(
    result_path: str,
    step: int,
    total: int,
    label: str,
    detail: str = "",
    *,
    phase: str | None = None,
) -> None:
    """Write progress info to progress.json next to result.json."""
    try:
        p = Path(result_path).parent / "progress.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        timestamp = _progress_timestamp()
        with PROGRESS_WRITE_LOCK:
            current: dict[str, Any] = {}
            if p.exists():
                try:
                    current = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    current = {}
            normalized_label = label
            normalized_detail = detail
            if _looks_mojibake(normalized_label):
                previous_label = current.get("label")
                if isinstance(previous_label, str) and previous_label.strip() and not _looks_mojibake(previous_label):
                    normalized_label = previous_label
            if _looks_mojibake(normalized_detail):
                previous_detail = current.get("detail")
                if isinstance(previous_detail, str) and previous_detail.strip() and not _looks_mojibake(previous_detail):
                    normalized_detail = previous_detail
            events = current.get("events")
            if not isinstance(events, list):
                events = []
            event: dict[str, Any] = {
                "timestamp": timestamp,
                "step": step,
                "total": total,
                "label": normalized_label,
            }
            if normalized_detail:
                event["detail"] = normalized_detail
            if phase:
                event["phase"] = phase
                event["phase_label"] = _progress_phase_label(phase)
            last_event = events[-1] if events else None
            if not isinstance(last_event, dict) or any(last_event.get(key) != event.get(key) for key in ("step", "total", "label", "detail", "phase")):
                events.append(event)
            payload: dict[str, Any] = {
                "step": step,
                "total": total,
                "label": normalized_label,
                "detail": normalized_detail,
                "updated_at": timestamp,
                "events": events[-MAX_PROGRESS_EVENTS:],
            }
            if phase:
                payload["phase"] = phase
                payload["phase_label"] = _progress_phase_label(phase)
            p.write_text(safe_json_dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


@router.post("/paper")
async def generate_topic_paper(req: PaperDraftRequest):
    try:
        result = await asyncio.to_thread(_generate_topic_paper_sync, req.model_dump())
        return {"success": True, "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/project-paper")
async def generate_project_paper(req: ProjectPaperRequest):
    try:
        result = await asyncio.to_thread(_generate_project_paper_sync, req.model_dump())
        return {"success": True, "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/proposal/start")
async def start_generate_proposal(req: TopicRequest):
    task = TASK_MANAGER.start("proposal", req.model_dump())
    return {"success": True, "data": task}


@router.post("/presentation/start")
async def start_generate_presentation(req: PresentationRequest):
    task = TASK_MANAGER.start("presentation", req.model_dump())
    return {"success": True, "data": task}


@router.post("/literature-review/start")
async def start_generate_literature_review(req: TopicRequest):
    task = TASK_MANAGER.start("literature_review", req.model_dump())
    return {"success": True, "data": task}


@router.post("/refine/start")
async def start_refine_draft(req: RefinementRequest):
    task = TASK_MANAGER.start("refinement", req.model_dump())
    return {"success": True, "data": task}


@router.post("/research-qa/start")
async def start_answer_research_question(req: ResearchQuestionRequest):
    task = TASK_MANAGER.start("research_qa", req.model_dump())
    return {"success": True, "data": task}


@router.post("/export-docx/start")
async def start_export_docx(req: ExportDocxRequest):
    task = TASK_MANAGER.start("export_docx", req.model_dump())
    return {"success": True, "data": task}


@router.post("/export-pptx/start")
async def start_export_pptx(req: ExportPptxRequest):
    task = TASK_MANAGER.start("export_pptx", req.model_dump())
    return {"success": True, "data": task}


@router.post("/paper/start")
async def start_topic_paper(req: PaperDraftRequest):
    task = TASK_MANAGER.start("paper", req.model_dump())
    return {"success": True, "data": task}


@router.post("/project-paper/start")
async def start_project_paper(req: ProjectPaperRequest):
    task = TASK_MANAGER.start("project", req.model_dump())
    return {"success": True, "data": task}


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    try:
        task = TASK_MANAGER.get(task_id)
        return {"success": True, "data": task}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    try:
        task = TASK_MANAGER.cancel(task_id)
        return {"success": True, "data": task}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc
