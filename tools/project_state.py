from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.project_models import dedupe_papers, normalize_paper_dict


STATE_FILENAME = "project_state.json"
PAPER_INDEX_PATH = Path("knowledge-base") / "paper_index.json"
KEYWORD_ROUTE_HINTS = [
    (["proposal", "开题", "计划", "research plan"], "/proposal"),
    (["focus", "方向", "想法", "problem framing"], "/focus"),
    (["verify", "验证", "查证", "fact check", "literature review"], "/lit-verify"),
    (["download", "zotero", "pdf", "下载"], "/lit-download"),
    (["review", "survey", "综述"], "/review-write"),
    (["paper", "论文", "experiment", "baseline", "ablation", "admet"], "/paper-write"),
]
STAGE_DEFAULT_ROUTE = {
    "focus": "/focus",
    "literature": "/lit-verify",
    "structure": "/proposal",
    "writing": "/review-write",
    "complete": "/review-write",
}
ROUTE_LABELS = {
    "/focus": "Phase 1: refine the research question",
    "/lit-verify": "Phase 2: verify literature and enrich evidence",
    "/lit-download": "Phase 2: download PDFs and sync Zotero",
    "/proposal": "Phase 3: freeze structure and generate outline",
    "/review-write": "Phase 4: draft or revise a literature review",
    "/paper-write": "Phase 4: draft a research paper and experiment plan",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_root(project_root: str | Path = ".") -> Path:
    return Path(project_root).resolve()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return ""


def _relative_files(root: Path, directory: Path, patterns: Iterable[str]) -> list[str]:
    if not directory.exists():
        return []

    files: set[str] = set()
    for pattern in patterns:
        for path in directory.glob(pattern):
            if path.is_file():
                files.add(str(path.relative_to(root)).replace("\\", "/"))
    return sorted(files)


def _outline_frozen(outline_path: Path) -> bool:
    if not outline_path.exists():
        return False

    header = "\n".join(_read_text(outline_path).splitlines()[:20])
    return bool(re.search(r"^status:\s*frozen\b", header, re.MULTILINE | re.IGNORECASE))


def _match_keyword_route(arguments: str) -> str | None:
    normalized = arguments.strip().lower()
    if not normalized:
        return None

    for keywords, route in KEYWORD_ROUTE_HINTS:
        if any(keyword in normalized for keyword in keywords):
            return route
    return None


def summarize_stage_gap(state: dict[str, Any]) -> str:
    stage = state.get("current_stage", "focus")
    artifacts = state.get("artifacts", {})

    if stage == "focus":
        return "No literature base detected yet; start by narrowing the question and collecting search terms."
    if stage == "literature":
        return "Literature assets exist, but the outline is missing; verify coverage and prepare a frozen structure."
    if stage == "structure":
        return "An outline exists but is not frozen; confirm the chapter plan before long-form drafting."
    if stage == "writing":
        draft_count = artifacts.get("draft_files", 0)
        return f"Writing is in progress with {draft_count} draft file(s); continue chapter work and fill citation gaps."
    return "Deliverables already exist; review, polish, or branch into a new research question."


def detect_project_state(project_root: str | Path = ".") -> dict[str, Any]:
    root = _resolve_root(project_root)
    knowledge_base = root / "knowledge-base"
    papers_dir = root / "papers"
    drafts_dir = root / "drafts"
    output_dir = root / "output"
    outline_path = root / "outline.md"
    paper_index_path = root / PAPER_INDEX_PATH

    knowledge_notes = _relative_files(root, knowledge_base, ["*.md", "**/*.md"])
    paper_files = _relative_files(root, papers_dir, ["*.pdf"])
    draft_files = _relative_files(root, drafts_dir, ["*.md", "**/*.md"])
    output_files = _relative_files(root, output_dir, ["*.md", "*.pdf", "*.docx", "*.pptx", "**/*.md", "**/*.pdf", "**/*.docx", "**/*.pptx"])

    paper_index_count = len(load_paper_index(root)) if paper_index_path.exists() else 0
    bib_exists = (knowledge_base / "sources.bib").exists()
    outline_exists = outline_path.exists()
    outline_frozen = _outline_frozen(outline_path)
    experiment_plan_exists = (drafts_dir / "experiment-plan.md").exists()
    has_literature = bool(knowledge_notes or paper_files or paper_index_count or bib_exists)

    if output_files:
        current_stage = "complete"
    elif draft_files or outline_frozen:
        current_stage = "writing"
    elif outline_exists:
        current_stage = "structure"
    elif has_literature:
        current_stage = "literature"
    else:
        current_stage = "focus"

    stage_status = {
        "focus": "completed" if current_stage != "focus" else "in_progress",
        "literature": "completed" if current_stage in {"structure", "writing", "complete"} and has_literature else ("in_progress" if current_stage == "literature" else "pending"),
        "structure": "completed" if current_stage in {"writing", "complete"} and outline_exists else ("in_progress" if current_stage == "structure" else "pending"),
        "writing": "completed" if current_stage == "complete" else ("in_progress" if current_stage == "writing" else "pending"),
        "complete": "completed" if current_stage == "complete" else "pending",
    }

    state = {
        "project_root": str(root),
        "updated_at": _now_iso(),
        "current_stage": current_stage,
        "outline_frozen": outline_frozen,
        "artifacts": {
            "knowledge_base_entries": len(knowledge_notes),
            "paper_index_count": paper_index_count,
            "paper_files": len(paper_files),
            "draft_files": len(draft_files),
            "output_files": len(output_files),
            "has_sources_bib": bib_exists,
            "has_experiment_plan": experiment_plan_exists,
        },
        "paths": {
            "knowledge_base": knowledge_notes,
            "papers": paper_files,
            "drafts": draft_files,
            "output": output_files,
            "outline": str(outline_path.relative_to(root)).replace("\\", "/") if outline_exists else "",
            "paper_index": str(PAPER_INDEX_PATH).replace("\\", "/"),
        },
        "stage_status": stage_status,
    }
    state["summary"] = summarize_stage_gap(state)
    return state


def recommend_next_route(arguments: str = "", project_root: str | Path = ".") -> dict[str, Any]:
    state = detect_project_state(project_root)
    keyword_route = _match_keyword_route(arguments)
    stage_route = STAGE_DEFAULT_ROUTE.get(state["current_stage"], "/focus")
    recommended_route = keyword_route or stage_route
    rationale = []

    if keyword_route:
        rationale.append(f"Matched the request to `{keyword_route}` using keywords from the prompt.")
    else:
        rationale.append(f"Defaulted to `{stage_route}` from current stage `{state['current_stage']}`.")

    if state.get("outline_frozen"):
        rationale.append("Outline is already frozen, so long-form drafting can continue.")
    elif state["current_stage"] == "structure":
        rationale.append("An outline exists but still needs explicit freeze confirmation.")

    if state["artifacts"].get("paper_index_count"):
        rationale.append(f"Paper index already tracks {state['artifacts']['paper_index_count']} normalized record(s).")

    return {
        "arguments": arguments,
        "current_stage": state["current_stage"],
        "recommended_route": recommended_route,
        "route_label": ROUTE_LABELS.get(recommended_route, ""),
        "rationale": rationale,
        "state_summary": state.get("summary", ""),
        "state": state,
    }


def load_project_state(project_root: str | Path = ".") -> dict[str, Any] | None:
    state_path = _resolve_root(project_root) / STATE_FILENAME
    if not state_path.exists():
        return None

    return json.loads(_read_text(state_path))


def save_project_state(state: dict[str, Any], project_root: str | Path = ".") -> Path:
    root = _resolve_root(project_root)
    state_path = root / STATE_FILENAME
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return state_path


def sync_project_state(project_root: str | Path = ".") -> dict[str, Any]:
    state = detect_project_state(project_root)
    save_project_state(state, project_root)
    return state


def load_paper_index(project_root: str | Path = ".") -> list[dict[str, Any]]:
    root = _resolve_root(project_root)
    index_path = root / PAPER_INDEX_PATH
    if not index_path.exists():
        return []

    return json.loads(_read_text(index_path))


def save_paper_index(records: list[dict[str, Any]], project_root: str | Path = ".") -> Path:
    root = _resolve_root(project_root)
    index_path = root / PAPER_INDEX_PATH
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return index_path


def register_search_results(
    papers: list[dict[str, Any]],
    project_root: str | Path = ".",
    discipline: str = "generic",
    query: str | None = None,
) -> list[dict[str, Any]]:
    existing = load_paper_index(project_root)
    incoming = [
        normalize_paper_dict(
            paper,
            source=paper.get("source") or paper.get("_source"),
            discipline=discipline,
        )
        for paper in papers
    ]
    merged = dedupe_papers(existing + incoming)
    save_paper_index(merged, project_root)

    state = detect_project_state(project_root)
    if query:
        state["last_search"] = {
            "query": query,
            "discipline": discipline,
            "saved_records": len(incoming),
            "timestamp": _now_iso(),
        }
    save_project_state(state, project_root)
    return merged


def _main(args: list[str]) -> int:
    command = args[0] if args else "status"

    if command in {"status", "sync", "papers"}:
        project_root = args[1] if len(args) > 1 else "."
        if command == "status":
            print(json.dumps(detect_project_state(project_root), ensure_ascii=False, indent=2))
            return 0
        if command == "sync":
            print(json.dumps(sync_project_state(project_root), ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(load_paper_index(project_root), ensure_ascii=False, indent=2))
        return 0

    if command == "route":
        if len(args) > 1 and Path(args[1]).exists():
            project_root = args[1]
            arguments = " ".join(args[2:])
        else:
            project_root = "."
            arguments = " ".join(args[1:])
        print(json.dumps(recommend_next_route(arguments=arguments, project_root=project_root), ensure_ascii=False, indent=2))
        return 0

    print("Usage: python tools/project_state.py [status|sync|papers|route] [project_root] [arguments]")
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
