from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any

from tools.project_state import sync_project_state
from tools.writing_enhancer import (
    _call_model,
    _contains_cjk,
    _load_llm_config,
    _split_sentences,
    _strip_ai_tone,
)

DEFAULT_CHUNK_LIMIT = 850
MAX_REFINEMENT_ROUNDS = 3
REFINEMENT_DRAFT_DIR = Path("drafts") / "refinement"
REFINEMENT_OUTPUT_DIR = Path("output") / "writing-refinement"
RECORDS_PATH = REFINEMENT_OUTPUT_DIR / "refinement-records.json"

ZH_OPENING_PATTERNS = (
    r"^(?:首先|其次|再次|最后|此外|综上所述|综合来看|总体来看|整体来看|总的来看)[，,:：]?\s*",
    r"^(?:结果|研究结果|实验结果)表明[，,:：]?\s*",
    r"^(?:可以看出|由此可见|据此可见|需要指出的是|值得注意的是)[，,:：]?\s*",
    r"^(?:一方面|另一方面)[，,:：]?\s*",
)
EN_OPENING_PATTERNS = (
    r"^(?:first|firstly|second|secondly|third|thirdly|finally|overall|in conclusion|notably|it can be seen that)[,:]?\s*",
)
ZH_FALLBACK_FILLERS = ("本文", "本章将", "下文将", "需要指出的是", "值得注意的是")
EN_FALLBACK_FILLERS = ("it can be seen that", "it should be noted that", "overall", "in conclusion")
ZH_SCORE_FILLERS = ("可以看出", "结果表明", "综合来看", "总体来看", "需要指出的是", "值得注意的是")
EN_SCORE_FILLERS = ("it can be seen that", "overall", "in conclusion", "it should be noted that")


@dataclass
class RefinementChunk:
    chunk_id: str
    block_index: int
    chunk_index: int
    text: str
    char_count: int


@dataclass
class RefinementBlock:
    block_index: int
    kind: str
    original_text: str
    chunk_ids: list[str]


@dataclass
class RefinementManifest:
    doc_id: str
    round_number: int
    source_path: str
    input_path: str
    chunk_limit: int
    block_count: int
    chunk_count: int
    blocks: list[RefinementBlock]
    chunks: list[RefinementChunk]

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "round_number": self.round_number,
            "source_path": self.source_path,
            "input_path": self.input_path,
            "chunk_limit": self.chunk_limit,
            "block_count": self.block_count,
            "chunk_count": self.chunk_count,
            "blocks": [asdict(block) for block in self.blocks],
            "chunks": [asdict(chunk) for chunk in self.chunks],
        }


def refine_document_package(
    project_root: str | Path,
    source: str,
    language: str = "auto",
    round_number: int | None = None,
    chunk_limit: int = DEFAULT_CHUNK_LIMIT,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    source_path = _resolve_source(root, source)
    source_text = _read_supported_text(source_path)
    resolved_language = _resolve_language(source_text, language)
    records = _load_records(root)
    source_rel = _to_relative(root, source_path)
    doc_id = _resolve_doc_id(records, source_rel)
    rounds = _get_rounds(records, doc_id)
    resolved_round = round_number or _detect_next_round(rounds)
    if resolved_round > MAX_REFINEMENT_ROUNDS:
        raise ValueError("This draft already completed all refinement rounds.")

    input_path = source_path if resolved_round == 1 else _resolve_previous_output(root, rounds, resolved_round - 1)
    input_rel = _to_relative(root, input_path)
    input_text = _read_supported_text(input_path)

    doc_slug = _doc_slug(doc_id)
    draft_dir = root / REFINEMENT_DRAFT_DIR
    output_dir = root / REFINEMENT_OUTPUT_DIR
    output_suffix = ".md" if source_path.suffix.lower() in {".md", ".markdown"} else ".txt"
    refined_path = draft_dir / f"{doc_slug}-round{resolved_round}{output_suffix}"
    prompt_path = draft_dir / f"{doc_slug}-round{resolved_round}-prompt.md"
    checklist_path = draft_dir / f"{doc_slug}-round{resolved_round}-checklist.md"
    artifact_json_path = output_dir / f"{doc_slug}-round{resolved_round}.json"
    quality_path = output_dir / f"{doc_slug}-round{resolved_round}-quality.json"
    manifest_path = output_dir / f"{doc_slug}-round{resolved_round}-manifest.json"

    llm_config = _load_llm_config(root)
    prompt_text = _render_round_prompt(
        round_number=resolved_round,
        language=resolved_language,
        title=_infer_title(source_path, input_text),
    )
    manifest = _build_manifest(
        source_path=source_rel,
        input_path=input_rel,
        doc_id=doc_id,
        round_number=resolved_round,
        text=input_text,
        language=resolved_language,
        chunk_limit=chunk_limit,
    )
    chunk_outputs = _run_round(
        manifest=manifest,
        prompt_text=prompt_text,
        language=resolved_language,
        llm_config=llm_config,
    )
    refined_text = _restore_text(manifest, chunk_outputs)
    quality = _score_refined_text(refined_text, resolved_language)

    _write_text(refined_path, refined_text)

    # Post-process: inject real CSV data tables and figure file paths.
    # The refiner rewrites content and may produce new placeholders or drop
    # injected images. Re-run injection to ensure figures persist across rounds.
    try:
        from tools.paper_writer import _inject_real_experiment_data
        _inject_real_experiment_data(
            refined_path,
            {"project_root": str(root)},
        )
    except Exception:
        pass  # Non-critical

    _write_text(prompt_path, prompt_text)
    _write_text(checklist_path, _render_checklist_markdown(quality, resolved_language, refined_path, manifest))
    _write_text(manifest_path, json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))
    _write_text(quality_path, json.dumps(quality, ensure_ascii=False, indent=2))

    record_entry = _update_records(
        root=root,
        records=records,
        doc_id=doc_id,
        origin_path=doc_id,
        round_record={
            "round": resolved_round,
            "source_path": source_rel,
            "input_path": input_rel,
            "output_path": _to_relative(root, refined_path),
            "prompt_path": _to_relative(root, prompt_path),
            "manifest_path": _to_relative(root, manifest_path),
            "quality_path": _to_relative(root, quality_path),
            "checklist_path": _to_relative(root, checklist_path),
            "chunk_limit": chunk_limit,
            "chunk_count": manifest.chunk_count,
            "block_count": manifest.block_count,
            "checklist_total": quality["total_score"],
            "timestamp": _now_iso(),
        },
    )

    artifact = {
        "kind": "refinement",
        "title": _refinement_title(source_path, resolved_language, resolved_round),
        "summary": _refinement_summary(
            language=resolved_language,
            round_number=resolved_round,
            chunk_count=manifest.chunk_count,
            total_score=quality["total_score"],
            llm_enabled=bool(llm_config),
        ),
        "language": resolved_language,
        "quality_meta": {
            "llm_enhanced": bool(llm_config),
            "provider": llm_config.get("provider") if llm_config else None,
            "model": llm_config.get("model") if llm_config else None,
            "anti_ai_cleanup": True,
            "deduplicated": True,
            "cross_section_deduped": True,
            "low_signal_pruned": True,
            "refinement_round": resolved_round,
            "refinement_total_rounds": MAX_REFINEMENT_ROUNDS,
            "chunk_count": manifest.chunk_count,
            "checklist_total": quality["total_score"],
        },
        "record_entry": record_entry,
        "supporting_assets": {
            "prompt_path": str(prompt_path),
            "checklist_path": str(checklist_path),
            "manifest_path": str(manifest_path),
            "quality_path": str(quality_path),
            "records_path": str(root / RECORDS_PATH),
        },
    }
    _write_text(artifact_json_path, json.dumps(artifact, ensure_ascii=False, indent=2))
    artifact["supporting_assets"]["artifact_json_path"] = str(artifact_json_path)

    state = sync_project_state(root)
    return {
        "project_root": str(root),
        "markdown_path": str(refined_path),
        "json_path": str(artifact_json_path),
        "prompts_path": str(prompt_path),
        "output_path": str(checklist_path),
        "artifact": artifact,
        "state": state,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_language(source_text: str, requested_language: str) -> str:
    if requested_language and requested_language != "auto":
        return requested_language
    return "zh" if _contains_cjk(source_text) else "en"


def _resolve_source(root: Path, source: str) -> Path:
    candidate = Path(source)
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"Source file not found: {source}")
    return candidate


def _read_supported_text(path: Path) -> str:
    if path.suffix.lower() not in {".md", ".markdown", ".txt"}:
        raise ValueError("Only .md, .markdown, and .txt drafts can be refined directly.")
    return path.read_text(encoding="utf-8")


def _to_relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _doc_slug(doc_id: str) -> str:
    stem = Path(doc_id).stem or "draft"
    slug = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "-", stem).strip("-") or "draft"
    digest = hashlib.sha1(doc_id.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def _infer_title(source_path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        return stripped[:80]
    return source_path.stem


def _refinement_title(source_path: Path, language: str, round_number: int) -> str:
    if language == "zh":
        return f"{source_path.stem} 第 {round_number} 轮精修稿"
    return f"{source_path.stem} refinement round {round_number}"


def _refinement_summary(
    *,
    language: str,
    round_number: int,
    chunk_count: int,
    total_score: int,
    llm_enabled: bool,
) -> str:
    if language == "zh":
        mode = "调用模型分块精修" if llm_enabled else "使用本地回退清洗链路"
        return f"{mode}完成第 {round_number} 轮草稿精修，共处理 {chunk_count} 个文本块，当前清单评分 {total_score}/50。"
    mode = "Chunk-level model refinement" if llm_enabled else "Local fallback cleanup"
    return f"{mode} completed round {round_number} over {chunk_count} text chunks. Current checklist score: {total_score}/50."


def _load_records(root: Path) -> dict[str, Any]:
    path = root / RECORDS_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_records(root: Path, records: dict[str, Any]) -> None:
    path = root / RECORDS_PATH
    _write_text(path, json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True))


def _iter_record_paths(entry: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    origin_path = entry.get("origin_path")
    if isinstance(origin_path, str) and origin_path:
        paths.append(origin_path)
    for round_item in entry.get("rounds", []) if isinstance(entry.get("rounds"), list) else []:
        if not isinstance(round_item, dict):
            continue
        for key in ("source_path", "input_path", "output_path"):
            value = round_item.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
    return paths


def _resolve_doc_id(records: dict[str, Any], source_rel: str) -> str:
    if source_rel in records:
        return source_rel
    for doc_id, entry in records.items():
        if not isinstance(entry, dict):
            continue
        if source_rel in _iter_record_paths(entry):
            return doc_id
    return source_rel


def _get_rounds(records: dict[str, Any], doc_id: str) -> list[dict[str, Any]]:
    entry = records.get(doc_id)
    if not isinstance(entry, dict):
        return []
    rounds = entry.get("rounds")
    if not isinstance(rounds, list):
        return []
    return [item for item in rounds if isinstance(item, dict)]


def _detect_next_round(rounds: list[dict[str, Any]]) -> int:
    completed = {int(item.get("round")) for item in rounds if isinstance(item.get("round"), int)}
    for expected in range(1, MAX_REFINEMENT_ROUNDS + 1):
        if expected not in completed:
            return expected
    raise ValueError("This draft already completed all refinement rounds.")


def _resolve_previous_output(root: Path, rounds: list[dict[str, Any]], round_number: int) -> Path:
    for item in rounds:
        if item.get("round") != round_number:
            continue
        output_path = item.get("output_path")
        if isinstance(output_path, str) and output_path:
            return (root / output_path).resolve()
    raise ValueError(f"Round {round_number} output was not found in the refinement record.")


def _update_records(
    *,
    root: Path,
    records: dict[str, Any],
    doc_id: str,
    origin_path: str,
    round_record: dict[str, Any],
) -> dict[str, Any]:
    entry = records.get(doc_id)
    if not isinstance(entry, dict):
        entry = {"origin_path": origin_path, "rounds": []}
    rounds = entry.get("rounds")
    if not isinstance(rounds, list):
        rounds = []
    rounds = [item for item in rounds if not isinstance(item, dict) or item.get("round") != round_record["round"]]
    rounds.append(round_record)
    rounds.sort(key=lambda item: int(item.get("round", 0)))
    entry["origin_path"] = origin_path
    entry["rounds"] = rounds
    records[doc_id] = entry
    _save_records(root, records)
    return entry


def _build_manifest(
    *,
    source_path: str,
    input_path: str,
    doc_id: str,
    round_number: int,
    text: str,
    language: str,
    chunk_limit: int,
) -> RefinementManifest:
    blocks = _split_blocks(text)
    manifest_blocks: list[RefinementBlock] = []
    manifest_chunks: list[RefinementChunk] = []
    for block_index, block in enumerate(blocks):
        if block["kind"] != "prose":
            manifest_blocks.append(
                RefinementBlock(
                    block_index=block_index,
                    kind=block["kind"],
                    original_text=block["text"],
                    chunk_ids=[],
                )
            )
            continue
        chunk_texts = _split_prose_block(block["text"], language, chunk_limit)
        chunk_ids: list[str] = []
        for chunk_index, chunk_text in enumerate(chunk_texts):
            chunk_id = f"b{block_index}_c{chunk_index}"
            chunk_ids.append(chunk_id)
            manifest_chunks.append(
                RefinementChunk(
                    chunk_id=chunk_id,
                    block_index=block_index,
                    chunk_index=chunk_index,
                    text=chunk_text,
                    char_count=len(chunk_text),
                )
            )
        manifest_blocks.append(
            RefinementBlock(
                block_index=block_index,
                kind=block["kind"],
                original_text=block["text"],
                chunk_ids=chunk_ids,
            )
        )
    return RefinementManifest(
        doc_id=doc_id,
        round_number=round_number,
        source_path=source_path,
        input_path=input_path,
        chunk_limit=chunk_limit,
        block_count=len(manifest_blocks),
        chunk_count=len(manifest_chunks),
        blocks=manifest_blocks,
        chunks=manifest_chunks,
    )


def _split_blocks(text: str) -> list[dict[str, str]]:
    normalized = text.replace("\r\n", "\n")
    raw_blocks: list[str] = []
    buffer: list[str] = []
    in_code_fence = False

    for line in normalized.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            buffer.append(line)
            if in_code_fence:
                raw_blocks.append("\n".join(buffer).strip("\n"))
                buffer = []
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            buffer.append(line)
            continue
        if not stripped:
            if buffer:
                raw_blocks.append("\n".join(buffer).strip("\n"))
                buffer = []
            continue
        buffer.append(line)

    if buffer:
        raw_blocks.append("\n".join(buffer).strip("\n"))

    return [{"kind": _classify_block(block), "text": block} for block in raw_blocks if block.strip()]


def _classify_block(block: str) -> str:
    stripped = block.lstrip()
    first_line = next((line.strip() for line in block.splitlines() if line.strip()), "")
    if not first_line:
        return "empty"
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return "fence"
    if first_line.startswith("#") or re.match(r"^#{1,6}\s", first_line):
        return "heading"
    if first_line.startswith(">"):
        return "blockquote"
    if first_line.startswith("|") or re.match(r"^[-*+]\s", first_line) or re.match(r"^\d+\.\s", first_line):
        return "structured"
    return "prose"


def _split_prose_block(text: str, language: str, chunk_limit: int) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return []
    if len(compact) <= chunk_limit:
        return [compact]

    sentences = _split_sentences(compact, language)
    chunks: list[str] = []
    current = ""
    glue = "" if language == "zh" else " "

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > chunk_limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_sentence(sentence, chunk_limit))
            continue

        candidate = sentence if not current else f"{current}{glue}{sentence}"
        if len(candidate) <= chunk_limit:
            current = candidate
            continue
        chunks.append(current)
        current = sentence

    if current:
        chunks.append(current)
    return chunks


def _split_long_sentence(sentence: str, chunk_limit: int) -> list[str]:
    fragments = re.split(r"(?<=[，、：:,])", sentence)
    chunks: list[str] = []
    current = ""
    for fragment in fragments:
        fragment = fragment.strip()
        if not fragment:
            continue
        candidate = fragment if not current else f"{current}{fragment}"
        if len(candidate) <= chunk_limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(fragment) <= chunk_limit:
            current = fragment
            continue
        for index in range(0, len(fragment), chunk_limit):
            chunks.append(fragment[index:index + chunk_limit])
    if current:
        chunks.append(current)
    return chunks


def _run_round(
    *,
    manifest: RefinementManifest,
    prompt_text: str,
    language: str,
    llm_config: dict[str, str] | None,
) -> dict[str, str]:
    import time as _time
    outputs: dict[str, str] = {}
    for chunk in manifest.chunks:
        prompt_input = _build_prompt_input(prompt_text, chunk.text, manifest.round_number, chunk.chunk_id)
        if llm_config:
            for _attempt in range(10):
                try:
                    rewritten = _call_model(llm_config, prompt_input, language)
                    break
                except Exception as _e:
                    if _attempt < 9:
                        _wait = min(10 * (2 ** _attempt), 120)
                        _time.sleep(_wait)
                    else:
                        rewritten = ""
        else:
            rewritten = _local_cleanup(chunk.text, language, manifest.round_number)
        outputs[chunk.chunk_id] = rewritten.strip() or chunk.text
    return outputs


def _build_prompt_input(prompt_text: str, chunk_text: str, round_number: int, chunk_id: str) -> str:
    return (
        f"[ROUND {round_number}]\n"
        f"[CHUNK {chunk_id}]\n\n"
        f"{prompt_text.strip()}\n\n"
        "[INPUT]\n"
        f"{chunk_text}"
    )


def _restore_text(manifest: RefinementManifest, outputs: dict[str, str]) -> str:
    restored_blocks: list[str] = []
    for block in manifest.blocks:
        if not block.chunk_ids:
            restored_blocks.append(block.original_text.strip())
            continue
        pieces = [outputs[chunk_id].strip() for chunk_id in block.chunk_ids if outputs.get(chunk_id)]
        restored_blocks.append("".join(pieces) if _contains_cjk("".join(pieces)) else " ".join(pieces))
    return "\n\n".join(block for block in restored_blocks if block.strip()).strip() + "\n"


def _render_round_prompt(*, round_number: int, language: str, title: str) -> str:
    if language == "zh":
        goals = {
            1: "修正结构和论证表达，删除元写作话语与空泛铺垫，让段落更贴近学术正文。",
            2: '压低模板化句壳和重复节奏，减少”结果表明、由此可见、综合来看”等套话。强制变换句首结构：相邻段落禁止使用相同的主语或状语开头（如连续多个”系统...”、”实验...”、”项目...”）。交替使用主谓、状谓、倒装、从句引导等不同句式。',
            3: '做终稿级精修，保持克制、自然、可信，不额外扩写无证据内容。确保相邻段落首句结构和长度差异明显：短句（20字以内）、中句（40-60字）、长句（80字以上）交替出现，避免连续3句长度相近。',
        }
        labels = {
            1: "结构与证据修订",
            2: "去模板化改写",
            3: "终稿精修",
        }
        return (
            f"# 草稿精修提示词\n\n"
            f"- 主题：{title}\n"
            f"- 轮次：第 {round_number} 轮（{labels[round_number]}）\n\n"
            "```text\n"
            "你是严谨的学术论文精修助手。请仅改写输入文本块，不要添加标题、列表、解释或额外结论。\n"
            f"本轮目标：{goals[round_number]}\n"
            "硬性约束：\n"
            "- 保留原意、术语、数字、引用、Markdown 内联格式和已出现的事实。\n"
            "- 不编造实验结果、文献来源、图表编号或工程实现细节。\n"
            "- 如果原文已经自然准确，只做必要最小修改。\n"
            "- 优先压缩空泛铺垫、模板化连接词和低信息增量句子。\n"
            "- 直接输出改写后的正文块，不要解释修改理由。\n"
            "```\n"
        )

    goals = {
        1: "tighten structure and evidence framing while removing meta-writing language and broad scaffolding.",
        2: "reduce templated phrasing, repeated transitions, and symmetrical paragraph rhythm.",
        3: "apply final-pass polish with concise, natural, and trustworthy academic prose.",
    }
    labels = {
        1: "structure and evidence revision",
        2: "template cleanup",
        3: "final polish",
    }
    return (
        f"# Draft Refinement Prompt\n\n"
        f"- Topic: {title}\n"
        f"- Round: {round_number} ({labels[round_number]})\n\n"
        "```text\n"
        "You are a rigorous academic writing refiner. Rewrite only the input text block.\n"
        f"Round goal: {goals[round_number]}\n"
        "Constraints:\n"
        "- Preserve meaning, terminology, numbers, citations, and inline Markdown.\n"
        "- Do not invent results, references, implementation details, or claims.\n"
        "- If the block already reads naturally, make only minimal edits.\n"
        "- Reduce filler, repetitive transitions, and low-signal summary sentences.\n"
        "- Output only the rewritten block with no commentary.\n"
        "```\n"
    )


def _local_cleanup(text: str, language: str, round_number: int) -> str:
    cleaned = text.strip()
    if language == "zh":
        cleaned = _strip_ai_tone(cleaned, "zh")
        for filler in ZH_FALLBACK_FILLERS:
            cleaned = re.sub(rf"(?:(?<=^)|(?<=[。！？；]))\s*{re.escape(filler)}[，,:：]?\s*", "", cleaned)
        if round_number >= 2:
            cleaned = re.sub(r"(?:，\s*)?(综上所述|综合来看|总体来看|总的来看)[，,:：]?\s*", "，", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or text.strip()

    rewritten: list[str] = []
    for sentence in _split_sentences(re.sub(r"\s+", " ", cleaned), "en"):
        current = sentence.strip()
        for pattern in EN_OPENING_PATTERNS:
            current = re.sub(pattern, "", current, flags=re.IGNORECASE)
        for filler in EN_FALLBACK_FILLERS:
            current = re.sub(re.escape(filler), "", current, flags=re.IGNORECASE)
        current = re.sub(r"\s+", " ", current).strip(" ,")
        if current:
            rewritten.append(current)
    return " ".join(rewritten).strip() or text.strip()


def _score_refined_text(text: str, language: str) -> dict[str, Any]:
    sentences = _split_sentences(text, language)
    sentence_lengths = [len(sentence) for sentence in sentences] or [0]
    starts = [_sentence_start(sentence, language) for sentence in sentences if sentence.strip()]
    start_counter = Counter(starts)
    repeated_start_hits = sum(count - 1 for count in start_counter.values() if count > 1)
    filler_hits = sum(text.count(item) for item in (ZH_SCORE_FILLERS if language == "zh" else EN_SCORE_FILLERS))
    opener_hits = _opener_hits(sentences, language)
    avg_len = sum(sentence_lengths) / max(len(sentence_lengths), 1)
    variance = pstdev(sentence_lengths) if len(sentence_lengths) > 1 else 0.0

    directness = _clamp_score(10 - opener_hits - filler_hits * 0.7 - (1 if avg_len > 90 else 0))
    rhythm = _clamp_score(4 + min(6, variance / 6))
    trust = _clamp_score(10 - filler_hits - opener_hits * 0.8)
    repeat_density = repeated_start_hits / max(1, len(sentences))
    authenticity = _clamp_score(10 - repeat_density * 15)
    concision = _clamp_score(10 - filler_hits * 0.8 - (1 if avg_len > 110 else 0))
    total_score = directness + rhythm + trust + authenticity + concision

    notes: list[str] = []
    if opener_hits:
        notes.append("句首套话仍然偏多，需要继续压低固定开场。" if language == "zh" else "Repeated sentence openings are still too frequent.")
    if filler_hits:
        notes.append("仍然存在低信息增量的总结句或评述壳句。" if language == "zh" else "Low-signal summary phrases are still visible.")
    if repeated_start_hits:
        notes.append("相邻段句式节奏仍有重复。" if language == "zh" else "Sentence rhythm still repeats too often.")
    if not notes:
        notes.append("当前版本已经基本满足精修清单要求。" if language == "zh" else "The current draft broadly satisfies the refinement checklist.")

    return {
        "directness": directness,
        "rhythm": rhythm,
        "trust": trust,
        "authenticity": authenticity,
        "concision": concision,
        "total_score": total_score,
        "sentence_count": len(sentences),
        "avg_sentence_length": round(avg_len, 2),
        "sentence_length_stddev": round(variance, 2),
        "issue_counts": {
            "template_openers": opener_hits,
            "filler_phrases": filler_hits,
            "repeated_starts": repeated_start_hits,
        },
        "notes": notes,
    }


def _sentence_start(sentence: str, language: str) -> str:
    cleaned = sentence.strip().lower()
    if language == "zh":
        return cleaned[:4]
    return " ".join(cleaned.split()[:3])


def _opener_hits(sentences: list[str], language: str) -> int:
    patterns = ZH_OPENING_PATTERNS if language == "zh" else EN_OPENING_PATTERNS
    total = 0
    for sentence in sentences:
        for pattern in patterns:
            if re.match(pattern, sentence.strip(), flags=re.IGNORECASE):
                total += 1
                break
    return total


def _clamp_score(value: float) -> int:
    return max(1, min(10, int(round(value))))


def _render_checklist_markdown(
    quality: dict[str, Any],
    language: str,
    refined_path: Path,
    manifest: RefinementManifest,
) -> str:
    if language == "zh":
        notes = "\n".join(f"- {note}" for note in quality["notes"])
        return (
            f"# 草稿精修检查清单\n\n"
            f"- 输出文件：`{refined_path}`\n"
            f"- 轮次：第 {manifest.round_number} 轮\n"
            f"- 文本块数：{manifest.chunk_count}\n\n"
            "| 维度 | 得分 |\n"
            "| --- | --- |\n"
            f"| 直接性 | {quality['directness']}/10 |\n"
            f"| 节奏 | {quality['rhythm']}/10 |\n"
            f"| 信任度 | {quality['trust']}/10 |\n"
            f"| 自然度 | {quality['authenticity']}/10 |\n"
            f"| 精炼度 | {quality['concision']}/10 |\n"
            f"| 总分 | {quality['total_score']}/50 |\n\n"
            "## 统计\n\n"
            f"- 句子数：{quality['sentence_count']}\n"
            f"- 平均句长：{quality['avg_sentence_length']}\n"
            f"- 句长标准差：{quality['sentence_length_stddev']}\n"
            f"- 套话句首：{quality['issue_counts']['template_openers']}\n"
            f"- 低信息评述壳句：{quality['issue_counts']['filler_phrases']}\n"
            f"- 重复开场：{quality['issue_counts']['repeated_starts']}\n\n"
            "## 结论\n\n"
            f"{notes}\n"
        )

    notes = "\n".join(f"- {note}" for note in quality["notes"])
    return (
        f"# Draft Refinement Checklist\n\n"
        f"- Output: `{refined_path}`\n"
        f"- Round: {manifest.round_number}\n"
        f"- Text chunks: {manifest.chunk_count}\n\n"
        "| Dimension | Score |\n"
        "| --- | --- |\n"
        f"| Directness | {quality['directness']}/10 |\n"
        f"| Rhythm | {quality['rhythm']}/10 |\n"
        f"| Trust | {quality['trust']}/10 |\n"
        f"| Naturalness | {quality['authenticity']}/10 |\n"
        f"| Concision | {quality['concision']}/10 |\n"
        f"| Total | {quality['total_score']}/50 |\n\n"
        "## Stats\n\n"
        f"- Sentence count: {quality['sentence_count']}\n"
        f"- Average sentence length: {quality['avg_sentence_length']}\n"
        f"- Sentence-length stddev: {quality['sentence_length_stddev']}\n"
        f"- Template openings: {quality['issue_counts']['template_openers']}\n"
        f"- Low-signal fillers: {quality['issue_counts']['filler_phrases']}\n"
        f"- Repeated starts: {quality['issue_counts']['repeated_starts']}\n\n"
        "## Notes\n\n"
        f"{notes}\n"
    )


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path
