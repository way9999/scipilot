from __future__ import annotations

import json
import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.domain_utils import detect_domain, get_blueprint, get_evidence_terms
from tools.text_safety import safe_json_dumps, safe_write_text, sanitize_utf8_text
from tools.writing_profiles import build_profile_guardrails, get_opening_banlist

SETTINGS_RELATIVE_PATH = Path("scipilot") / "settings.json"
DEFAULT_TARGET_WORDS = {"zh": 15000, "en": 8000}
OPENING_BANLIST_ZH = (
    "首先",
    "其次",
    "再次",
    "最后",
    "综上所述",
    "值得注意的是",
    "需要指出的是",
    "可以看出",
)

ZH_SENTENCE_PREFIX_PATTERNS = (
    r"^(?:首先|其次|再次|最后|此外)[：:，,\s]*",
    r"^(?:结果|研究结果|实验结果)表明[：:，,\s]*",
    r"^(?:可以看出|由此可见|据此可见)[：:，,\s]*",
    r"^(?:综合来看|总体来看|整体来看|总的来看)[：:，,\s]*",
    r"^(?:进一步分析)?(?:可知|表明)[：:，,\s]*",
    r"^(?:值得注意的是|需要指出的是)[：:，,\s]*",
    r"^(?:一方面|另一方面)[：:，,\s]*",
)

ENGLISH_KEYWORD_STOPWORDS = {
    "analysis",
    "approach",
    "based",
    "framework",
    "method",
    "methods",
    "model",
    "models",
    "paper",
    "research",
    "review",
    "section",
    "study",
    "system",
    "using",
}

CHINESE_KEYWORD_STOPWORDS = {
    "研究",
    "方法",
    "模型",
    "实验",
    "系统",
    "分析",
    "应用",
    "设计",
    "实现",
    "综述",
}

ZH_CLOSING_PUNCTUATION = "，。！？；：、）》】」』"
ZH_OPENING_PUNCTUATION = "（《【「『"


def enhance_generated_paper_package(
    base_result: dict[str, Any],
    *,
    project_root: str | Path,
    topic: str,
    language: str,
    paper_type: str,
    project_context: dict[str, Any] | None,
    target_words: int | None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    artifact = dict(base_result.get("artifact") or {})
    resolved_language = _resolve_language(language, topic, artifact)
    normalized_target = _normalize_target_words(target_words, resolved_language)
    references = list(artifact.get("references") or [])
    llm_config = _load_llm_config(root, fallback_chain=True)
    blueprint = _select_blueprint(topic, resolved_language, project_context)
    existing_sections = _normalize_existing_sections(artifact.get("sections") or [], resolved_language)
    preserved_base_sections = bool(existing_sections)

    if llm_config:
        try:
            sections = _draft_sections_with_llm(
                llm_config=llm_config,
                topic=topic,
                language=resolved_language,
                paper_type=paper_type,
                references=references,
                project_context=project_context,
                blueprint=blueprint,
                target_words=normalized_target,
            )
        except Exception as exc:
            import traceback
            traceback.print_exc()
            llm_config = None
            sections = existing_sections or _build_fallback_sections(
                topic=topic,
                language=resolved_language,
                references=references,
                project_context=project_context,
                blueprint=blueprint,
            )
    else:
        sections = existing_sections or _build_fallback_sections(
            topic=topic,
            language=resolved_language,
            references=references,
            project_context=project_context,
            blueprint=blueprint,
        )

    sections = _dedupe_sections(sections, resolved_language)
    actual_words = _sections_word_count(sections, resolved_language)

    # Expand into a bounded target window: never stop below the requested
    # minimum, but also do not let the draft grow far past the requested size.
    _target_min = normalized_target
    _target_max = _target_max_words(normalized_target, resolved_language)
    # Normalize llm_config to a list for chain fallback
    _config_chain = []
    if isinstance(llm_config, list):
        _config_chain = list(llm_config)
    elif isinstance(llm_config, dict):
        _config_chain = [llm_config]

    _expand_round = 0
    max_expand_rounds = _max_expand_rounds(normalized_target, resolved_language)
    while _config_chain and actual_words < _target_min and _expand_round < max_expand_rounds:
        _expand_round += 1
        _remaining_gap = max(_target_min - actual_words, 0)
        try:
            sections = _expand_short_sections(
                llm_config=_config_chain,
                topic=topic,
                language=resolved_language,
                references=references,
                project_context=project_context,
                sections=sections,
                target_words=normalized_target,
                remaining_gap=_remaining_gap,
                target_max_words=_target_max,
                blueprint=blueprint,
            )
            sections = _dedupe_sections(sections, resolved_language)
            new_actual_words = _sections_word_count(sections, resolved_language)
            if new_actual_words > _target_max:
                sections, new_actual_words = _trim_sections_to_cap(
                    sections,
                    language=resolved_language,
                    max_words=_target_max,
                )
            actual_words = new_actual_words
            if actual_words >= _target_min:
                break
        except Exception:
            import traceback
            traceback.print_exc()
            break

    if actual_words > _target_max:
        sections, actual_words = _trim_sections_to_cap(
            sections,
            language=resolved_language,
            max_words=_target_max,
        )

    title = str(artifact.get("topic") or artifact.get("title") or topic).strip() or topic.strip()
    # If no title provided, generate one from content
    if not title and _config_chain:
        try:
            _first_section_text = "\n".join(str(p) for s in sections[:2] for p in (s.get("content", []) if isinstance(s, dict) else []))[:1500]
            _gen_prompt = (
                f"鏍规嵁浠ヤ笅璁烘枃姝ｆ枃鍐呭锛岀敓鎴愪竴涓畝娲佺殑涓枃姣曚笟璁烘枃鏍囬锛?5瀛椾互鍐咃紝涓嶈寮曞彿锛屼笉瑕佺紪鍙凤級銆傚彧杈撳嚭鏍囬锛歕n\n"
                f"{_first_section_text}"
            )
            for _cfg in _config_chain:
                try:
                    _gen_title = _call_model(_cfg, _gen_prompt, resolved_language).strip().strip('"').strip("'").strip("《》")
                    if _gen_title and len(_gen_title) <= 30:
                        title = _gen_title
                        print(f"[Title] 鑷姩鐢熸垚鏍囬: {title}")
                    break
                except Exception:
                    continue
        except Exception:
            pass
    if not title:
        title = "璁烘枃"
    markdown = _render_markdown(title, sections)
    outline = _render_outline(title, sections, resolved_language)
    plan = _render_plan(title, sections, resolved_language, normalized_target, actual_words)

    markdown_path = Path(str(base_result["markdown_path"]))
    safe_write_text(markdown_path, markdown, trailing_newline=True)

    # Post-process: inject real CSV data tables and figure file paths into the draft.
    # This is also called by _finalize_zh_generated_package, but running it here
    # ensures figures are injected even when the enhancer is called directly (e.g. from
    # a test script) without going through generate_paper_package.
    try:
        from paper_writer import _inject_real_experiment_data
        _inject_real_experiment_data(markdown_path, base_result)
    except Exception:
        pass  # Non-critical; _finalize_zh_generated_package may handle it later

    outline_path_value = base_result.get("outline_path")
    if outline_path_value:
        safe_write_text(Path(str(outline_path_value)), outline, trailing_newline=True)

    plan_path_value = base_result.get("plan_path")
    if plan_path_value:
        safe_write_text(Path(str(plan_path_value)), plan, trailing_newline=True)

    artifact["sections"] = sections
    # Resolve actual config used (may be list from fallback_chain)
    _used_config = None
    if isinstance(llm_config, list):
        _used_config = llm_config[0] if llm_config else None
    elif isinstance(llm_config, dict):
        _used_config = llm_config
    _was_enhanced = bool(sections) and _used_config is not None

    artifact["summary"] = _build_summary(
        language=resolved_language,
        llm_enabled=_was_enhanced,
        target_words=normalized_target,
        actual_words=actual_words,
    )
    artifact["target_words"] = normalized_target
    artifact["actual_words"] = actual_words
    artifact["quality_meta"] = {
        "llm_enhanced": _was_enhanced,
        "provider": _used_config.get("provider") if _used_config else None,
        "model": _used_config.get("model") if _used_config else None,
        "target_words": normalized_target,
        "actual_words": actual_words,
        "section_count": len(sections),
        "expansion_rounds": _expand_round,
        "deduplicated": True,
        "anti_ai_cleanup": True,
        "section_contextualized": True,
        "structure_guardrails": True,
        "reference_reranked": True,
        "cross_section_deduped": True,
        "scaffold_compressed": True,
        "low_signal_pruned": True,
        "result_placeholders_standardized": True,
        "base_sections_preserved": preserved_base_sections and not _was_enhanced,
        "section_evidence_packets": True,
        "critique_revision_enabled": _was_enhanced,
        "quality_review_threshold": SECTION_REVIEW_PASS_THRESHOLD,
        "figure_plan_anchored": bool((project_context or {}).get("figure_plan")),
    }

    json_path_value = base_result.get("json_path")
    if json_path_value:
        safe_write_text(
            Path(str(json_path_value)),
            safe_json_dumps(artifact, ensure_ascii=False, indent=2),
            trailing_newline=True,
        )

    base_result["artifact"] = artifact
    return base_result


def _resolve_language(requested_language: str, topic: str, artifact: dict[str, Any]) -> str:
    if requested_language and requested_language != "auto":
        return requested_language
    if artifact.get("language") in {"zh", "en"}:
        return str(artifact["language"])
    return "zh" if _contains_cjk(topic) else "en"


def _normalize_target_words(target_words: int | None, language: str) -> int:
    default_value = DEFAULT_TARGET_WORDS["zh" if language == 'zh' else "en"]
    if target_words is None:
        return default_value
    return max(1500 if language == 'zh' else 1000, min(int(target_words), 80000))


def _target_completion_ratio(target_words: int, language: str) -> float:
    if language == 'zh':
        if target_words >= 20000:
            return 0.96
        if target_words >= 12000:
            return 0.94
        return 0.90
    if target_words >= 12000:
        return 0.94
    return 0.90


def _max_expand_rounds(target_words: int, language: str) -> int:
    chunk_size = 3500 if language == 'zh' else 2200
    return max(4, min(18, target_words // chunk_size + 4))


def _section_draft_target_ratio(language: str) -> float:
    return 0.94 if language == 'zh' else 0.92


def _section_completion_ratio(language: str) -> float:
    return 0.96 if language == 'zh' else 0.94


def _target_max_words(target_words: int, language: str) -> int:
    overshoot_buffer = max(
        220 if language == 'zh' else 140,
        int(target_words * (0.03 if language == 'zh' else 0.04)),
    )
    return target_words + overshoot_buffer


def _final_fill_threshold(target_words: int, language: str) -> int:
    floor = 900 if language == 'zh' else 350
    ceiling = 2000 if language == 'zh' else 900
    ratio_value = int(target_words * (0.05 if language == 'zh' else 0.06))
    return min(ceiling, max(floor, ratio_value))


def _normalize_existing_sections(raw_sections: Any, language: str) -> list[dict[str, Any]]:
    if not isinstance(raw_sections, list):
        return []

    normalized_sections: list[dict[str, Any]] = []
    for raw_section in raw_sections:
        if not isinstance(raw_section, dict):
            continue
        title = str(raw_section.get("title") or "").strip()
        content_value = raw_section.get("content")
        if isinstance(content_value, str):
            content_items = _split_blocks(content_value, language)
        elif isinstance(content_value, list):
            content_items = [str(item).strip() for item in content_value if str(item).strip()]
        else:
            content_items = []
        if not title and not content_items:
            continue
        normalized_sections.append({"title": title, "content": content_items})
    return normalized_sections


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _load_llm_config(project_root: Path, fallback_chain: bool = False) -> dict[str, str] | None | list[dict[str, str]]:
    """Load LLM config from settings.json, with env-var fallback.

    When fallback_chain=True, returns a list of configs to try in order.
    Otherwise returns the first available config (or None).
    """
    settings_path = None
    env_settings_path = os.environ.get("SCIPILOT_SETTINGS_PATH", "").strip()
    if env_settings_path:
        candidate = Path(env_settings_path)
        if candidate.exists():
            settings_path = candidate

    if settings_path is None:
        candidate = project_root / SETTINGS_RELATIVE_PATH
        if candidate.exists():
            settings_path = candidate
        else:
            fallback = Path(__file__).resolve().parent.parent / SETTINGS_RELATIVE_PATH
            if fallback.exists():
                settings_path = fallback

    settings = None
    if settings_path and settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    configs: list[dict[str, str]] = []

    if settings:
        api_keys = settings.get("api_keys") or {}
        base_urls = settings.get("api_base_urls") or {}
        default_provider = str(settings.get("default_provider") or "llm").strip() or "llm"
        preferred_order = [default_provider, "llm", "ollama"]

        for provider in preferred_order:
            if not provider:
                continue
            if provider == "llm":
                key = str(
                    api_keys.get("llm")
                    or api_keys.get("openai")
                    or api_keys.get("claude")
                    or api_keys.get("anthropic")
                    or ""
                )
                base_url = str(
                    base_urls.get("llm")
                    or base_urls.get("openai")
                    or base_urls.get("claude")
                    or base_urls.get("anthropic")
                    or ""
                )
                model = str(settings.get("llm_model") or settings.get("default_model") or "").strip()
                if key and base_url:
                    configs.append({
                        "provider": "llm",
                        "model": model or _default_model_for_provider("llm"),
                        "base_url": base_url,
                        "api_key": key,
                    })
                continue

            if provider == "ollama":
                base_url = str(base_urls.get("ollama") or "")
                model = str(settings.get("ollama_model") or settings.get("default_model") or "").strip()
                if base_url:
                    configs.append({
                        "provider": "ollama",
                        "model": model or _default_model_for_provider("ollama"),
                        "base_url": base_url,
                        "api_key": "",
                    })
                continue

            # Handle named providers (openai, claude, anthropic, etc.)
            key = str(api_keys.get(provider) or "")
            base_url = str(base_urls.get(provider) or "")
            model = str(settings.get(f"{provider}_model") or settings.get("default_model") or "").strip()
            if provider == "claude":
                key = str(api_keys.get("claude") or api_keys.get("anthropic") or "")
            if key and base_url:
                configs.append({
                    "provider": provider,
                    "model": model or _default_model_for_provider(provider),
                    "base_url": base_url,
                    "api_key": key,
                })
                continue

        legacy_order = ["openai", "claude", "anthropic"]
        for provider in legacy_order:
            key = str(api_keys.get(provider) or "")
            base_url = str(base_urls.get(provider) or "")
            model = str(settings.get("default_model") or "").strip()
            if provider == "claude":
                key = str(api_keys.get("claude") or api_keys.get("anthropic") or "")
            if key and (base_url or provider in {"openai", "claude", "anthropic"}):
                configs.append({
                    "provider": provider,
                    "model": model or _default_model_for_provider(provider),
                    "base_url": base_url or _default_base_url_for_provider(provider),
                    "api_key": key,
                })

    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    env_base = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if env_key and env_base:
        env_model = os.environ.get("ANTHROPIC_MODEL", "glm-5-turbo").strip()
        configs.append({
            "provider": "anthropic",
            "model": env_model,
            "base_url": env_base,
            "api_key": env_key,
        })

    if fallback_chain:
        seen: set[tuple[str, str, str]] = set()
        unique: list[dict[str, str]] = []
        for c in configs:
            key = (c["provider"], c["base_url"], c["model"])
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique if unique else None
    return configs[0] if configs else None


def _default_model_for_provider(provider: str) -> str:
    if provider in {"claude", "anthropic"}:
        return "claude-sonnet-4-6"
    if provider == "ollama":
        return "qwen2.5"
    return "gpt-4o"


def _default_base_url_for_provider(provider: str) -> str:
    if provider in {"claude", "anthropic"}:
        return "https://api.anthropic.com/v1/messages"
    if provider == "ollama":
        return "http://localhost:11434/api/chat"
    return "https://api.openai.com/v1/chat/completions"


def _select_blueprint(topic: str, language: str, project_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Select paper structure blueprint based on detected domain archetype.

    Uses domain_utils.get_blueprint() which maps detected domain 鈫?archetype 鈫?chapter structure.
    For Chinese math-heavy projects, injects domain-specific formula derivation points.
    """
    is_math_heavy = _is_math_heavy_source(project_context)

    if language == 'zh' and is_math_heavy:
        # Use domain-aware blueprint but inject formula points into Chapter 2
        base_blueprint = get_blueprint(topic, project_context)
        enriched = []
        for spec in base_blueprint:
            points = list(spec["points"])
            title = str(spec["title"])
            # Enrich the theory/math chapter with formula derivation instructions
            if any(kw in title for kw in ("鐞嗚", "鏁板", "鍘熺悊", "妯″瀷", "鍏紡", "鍩虹")):
                formula_points = _build_algorithm_formula_points(project_context)
                points = formula_points + [p for p in points if p not in formula_points]
            enriched.append({**spec, "points": points})
        return enriched

    if language == 'zh':
        return get_blueprint(topic, project_context)

    # English: use engineering-style blueprint (already generic)
    return [
        {"title": "1. Introduction", "share": 0.15, "points": ["Background and motivation", "Related work gap", "Contributions and thesis organization"]},
        {"title": "2. Technical Background", "share": 0.17, "points": ["Core concepts", "Relevant methods", "Research gap"]},
        {"title": "3. Method and System Design", "share": 0.20, "points": ["Problem formulation", "System architecture", "Implementation strategy"]},
        {"title": "4. Implementation", "share": 0.22, "points": ["Environment", "Core modules", "Engineering trade-offs"]},
        {"title": "5. Experiments and Analysis", "share": 0.20, "points": ["Setup", "Results", "Discussion"]},
        {"title": "6. Conclusion", "share": 0.06, "points": ["Findings", "Limitations and future work"]},
    ]


def _dedupe_values(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        ordered.append(normalized)
        seen.add(normalized)
    return ordered


def _topic_keywords(text: str) -> list[str]:
    english_keywords = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9/_-]{2,}", text.lower())
        if token not in ENGLISH_KEYWORD_STOPWORDS
    ]

    chinese_keywords: list[str] = []
    split_tokens = (
        "\u4e2d\u7684",
        "\u53ca\u5176",
        "\u4ee5\u53ca",
        "\u57fa\u4e8e",
        "\u9762\u5411",
        "\u9488\u5bf9",
        "\u5173\u4e8e",
        "\u7528\u4e8e",
        "\u548c",
        "\u4e0e",
    )
    split_pattern = "|".join(re.escape(token) for token in split_tokens)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        parts = []
        for part in re.split(split_pattern, chunk):
            normalized_part = re.sub(r"^[\u7684\u4e0e\u548c\u53ca]+|[\u7684\u4e0e\u548c\u53ca]+$", "", part.strip())
            if len(normalized_part) < 2 or normalized_part in split_tokens:
                continue
            parts.append(normalized_part)
        if not parts:
            fallback_chunk = re.sub(r"^[\u7684\u4e0e\u548c\u53ca]+|[\u7684\u4e0e\u548c\u53ca]+$", "", chunk.strip())
            parts = [fallback_chunk] if len(fallback_chunk) >= 2 and fallback_chunk not in split_tokens else []
        chinese_keywords.extend(part for part in parts if part not in CHINESE_KEYWORD_STOPWORDS)

    return _dedupe_values([*english_keywords, *chinese_keywords])


def _detect_section_role(section_title: str, section_points: list[str]) -> str:
    haystack = " ".join([section_title, *section_points]).lower()
    if any(keyword in haystack for keyword in ("experiment", "evaluation", "result", "metric", "benchmark", "ablation", "瀹為獙", "缁撴灉", "璇勪及", "鎸囨爣", "娴嬭瘯", "娑堣瀺")):
        return "experiment"
    if any(keyword in haystack for keyword in ("implementation", "deploy", "module", "pipeline", "workflow", "realization", "瀹炵幇", "閮ㄧ讲", "妯″潡", "娴佺▼", "宸ョ▼")):
        return "implementation"
    if any(keyword in haystack for keyword in ("method", "design", "architecture", "scheme", "framework", "绯荤粺璁捐", "鎬讳綋璁捐", "鏂规硶", "鏋舵瀯", "鏂规", "妯″瀷")):
        return "design"
    if any(keyword in haystack for keyword in ("background", "related", "preliminar", "introduction", "缁", "寮曡█", "鑳屾櫙", "鐩稿叧宸ヤ綔", "鐞嗚鍩虹", "缁艰堪")):
        return "background"
    if any(keyword in haystack for keyword in ("conclusion", "future", "summary", "缁撹", "鎬荤粨", "灞曟湜")):
        return "conclusion"
    return "general"


def _section_keywords(topic: str, section_title: str, section_points: list[str]) -> list[str]:
    role = _detect_section_role(section_title, section_points)
    role_keywords = {
        "background": ["motivation", "background", "related", "gap", "鑳屾櫙", "鐩稿叧宸ヤ綔", "闂"],
        "design": ["architecture", "interface", "module", "method", "鏂规", "鏋舵瀯", "鎺ュ彛", "妯″潡"],
        "implementation": ["implementation", "module", "config", "pipeline", "瀹炵幇", "閰嶇疆", "娴佺▼", "宸ョ▼"],
        "experiment": ["experiment", "evaluation", "result", "metric", "benchmark", "瀹為獙", "璇勪及", "鎸囨爣", "缁撴灉"],
        "conclusion": ["finding", "limitation", "future", "缁撹", "涓嶈冻", "灞曟湜"],
    }
    keywords = _topic_keywords(topic)
    keywords.extend(_topic_keywords(section_title))
    for point in section_points:
        keywords.extend(_topic_keywords(point))
    keywords.extend(role_keywords.get(role, []))
    return _dedupe_values(keywords)[:20]


def _keyword_overlap_score(text: str, keywords: list[str]) -> int:
    haystack = text.lower()
    score = 0
    for keyword in keywords:
        needle = keyword if _contains_cjk(keyword) else keyword.lower()
        if needle and needle in haystack:
            score += 1
    return score


def _candidate_item_score(item: str, *, keywords: list[str], role: str) -> int:
    score = _keyword_overlap_score(item, keywords)
    lowered = item.lower()
    if role == "experiment" and any(hint in lowered for hint in ("result", "metric", "eval", "benchmark", "report", "accuracy", "loss", "auc", "f1")):
        score += 3
    if role in {"design", "implementation"} and any(hint in lowered for hint in ("src", "launch", "config", "service", "controller", "planner", "node", "yaml", ".py", ".ts", ".rs")):
        score += 2
    return score


def _select_relevant_items(items: list[str], *, keywords: list[str], role: str, limit: int) -> list[str]:
    normalized_items = [str(item).strip() for item in items if str(item).strip()]
    if not normalized_items:
        return []
    scored = [
        (_candidate_item_score(item, keywords=keywords, role=role), index, item)
        for index, item in enumerate(normalized_items)
    ]
    if any(score > 0 for score, _, _ in scored):
        ordered = [item for _, _, item in sorted(scored, key=lambda entry: (-entry[0], entry[1]))]
        return ordered[:limit]
    return normalized_items[:limit]


def _has_result_evidence(project_context: dict[str, Any] | None) -> bool:
    if not project_context:
        return False
    return bool((project_context.get("result_clues") or []) or (project_context.get("candidate_result_files") or []))


def _missing_experiment_evidence_notice(language: str, *, detailed: bool) -> str:
    if language == 'zh':
        if detailed:
            return "当前缺少可直接支撑定量结论的结果证据，本节应先固定实验设置、评价指标、对比基线与图表槽位，待补齐日志、指标汇总或截图后再回填定量结论。"
        return "当前缺少可直接支撑定量结论的结果证据，本节仅保留实验设置、评价指标、对比基线和图表槽位。"
    if detailed:
        return "Direct result evidence is currently unavailable, so this section should freeze evaluation setup, metrics, baselines, and figure/table slots until logs, metric summaries, or screenshots are added."
    return "Direct result evidence is currently unavailable, so this section should keep only evaluation setup, metrics, baselines, and figure/table slots."


def _fallback_experiment_slot_paragraph(topic: str, point: str, language: str) -> str:
    if language == 'zh':
        return f"{point}当前应围绕“{topic}”先明确实验环境、任务场景、评价指标、对比基线以及结果表和误差分析图的槽位定义，避免在证据补齐前提前写死性能结论。"
    return f"The subsection on {point} should currently lock down the evaluation environment, task setting, metrics, baselines, and figure/table slots for {topic} instead of hard-coding performance claims before evidence is collected."


def _fallback_point_focus_paragraph(*, topic: str, point: str, section_title: str, language: str) -> str:
    role = _detect_section_role(section_title, [point])
    if language == 'zh':
        if role == "background":
            return f"{point}应明确交代“{topic}”面对的问题场景、已有工作的处理边界以及当前研究尚未覆盖的空缺，使后文的方法与实现不至于脱离问题定义。"
        if role == "design":
            return f"{point}应突出“{topic}”在系统方案层面的模块拆分、接口关系和设计约束，说明该设计如何在复杂场景下保持可扩展性与可维护性。"
        if role == "implementation":
            return f'{point}需要落实到“{topic}”的代码组织、运行流程、配置依赖和模块协作，重点说明功能是如何真正落到工程实现上的。'
        if role == "experiment":
            return f"{point}应围绕“{topic}”的实验设置、评价指标、对比基线和误差来源展开，保证分析建立在可验证的测试流程之上。"
        if role == "conclusion":
            return f"{point}需要回收“{topic}”的主要发现、当前局限和后续工作方向，避免只重复前文章节标题而不形成收束性的判断。"
        return f"{point}这一部分应解释其在“{topic}”中的具体职责、约束条件与上下游关系，避免停留在抽象概念层面。"

    if role == "background":
        return f"The subsection on {point} should define the problem setting for {topic}, clarify the boundary of prior work, and expose the gap that motivates the rest of the paper."
    if role == "design":
        return f"The subsection on {point} should explain how {topic} is decomposed into modules, interfaces, and constraints so that the overall design remains defensible and extensible."
    if role == "implementation":
        return f"The subsection on {point} should stay close to code structure, runtime flow, configuration, and module coordination rather than repeating high-level terminology."
    if role == "experiment":
        return f"The subsection on {point} should focus on evaluation setup, metrics, baselines, and error sources so that later claims remain tied to verifiable evidence."
    if role == "conclusion":
        return f"The subsection on {point} should synthesize findings, limitations, and future work instead of restating earlier section headings."
    return f"The subsection on {point} should explain its role in the overall study, clarify the design constraints, and connect technical choices to the larger research objective."


def _draft_sections_with_llm(
    *,
    llm_config: dict[str, str] | list[dict[str, str]],
    topic: str,
    language: str,
    paper_type: str,
    references: list[dict[str, Any]],
    project_context: dict[str, Any] | None,
    blueprint: list[dict[str, Any]],
    target_words: int,
) -> list[dict[str, Any]]:
    # Normalize to a chain of configs for fallback
    if isinstance(llm_config, dict):
        _config_chain = [llm_config]
    else:
        _config_chain = list(llm_config)

    _working_config: dict[str, str] | None = None

    def _try_call_model(prompt: str) -> str:
        """Try each config in chain until one succeeds. Cache the working config."""
        nonlocal _working_config
        # If we have a known-working config, try it first
        if _working_config:
            try:
                return _call_model(_working_config, prompt, language)
            except Exception as exc:
                print(f"[LLM] Cached config failed, retrying chain: {exc}")
                _working_config = None  # reset cache
        last_err = None
        for cfg in _config_chain:
            try:
                result = _call_model(cfg, prompt, language)
                _working_config = cfg  # cache for next call
                return result
            except Exception as exc:
                last_err = exc
                print(f"[LLM] Config failed ({cfg.get('provider')}/{cfg.get('model')}): {exc}")
                continue
        raise last_err  # type: ignore

    sections: list[dict[str, Any] | None] = [None] * len(blueprint)
    total_sections = len(blueprint)

    def _draft_one(spec_idx_and_spec: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        spec_idx, spec = spec_idx_and_spec
        section_title = str(spec["title"])
        try:
            from sidecar.routers.writing import _section_progress_callback
            _section_progress_callback(spec_idx, total_sections, section_title, "drafting")
        except Exception:
            pass
        section_points = [str(item) for item in spec["points"]]
        section_target = max(
            500 if language == 'zh' else 300,
            int(target_words * float(spec["share"]) * _section_draft_target_ratio(language)),
        )
        section_target = min(section_target, int(target_words * 0.38))
        reference_brief = _build_reference_brief(
            references,
            topic=topic,
            section_title=section_title,
            section_points=section_points,
            language=language,
        )
        project_brief = _build_curated_project_brief(
            project_context,
            language,
            topic=topic,
            section_title=section_title,
            section_points=section_points,
        )
        section_notes = _build_curated_section_guardrails(
            language=language,
            section_title=section_title,
            section_points=section_points,
            has_project_context=bool(project_context),
            has_result_evidence=_has_result_evidence(project_context),
            has_reference_support=bool(references),
        )
        evidence_packet = _build_section_evidence_packet(
            topic=topic,
            language=language,
            section_title=section_title,
            section_points=section_points,
            project_context=project_context,
            project_brief=project_brief,
            reference_brief=reference_brief,
        )
        prompt = _build_section_prompt(
            topic=topic,
            language=language,
            paper_type=paper_type,
            section_title=section_title,
            section_points=section_points,
            section_target=section_target,
            evidence_packet=evidence_packet,
            section_notes=section_notes,
        )
        drafted = _try_call_model(prompt)
        return spec_idx, {
            "title": section_title,
            "content": _split_blocks(_sanitize_section_output(drafted, section_title, language, section_points=section_points), language),
        }

    max_workers = min(3, len(blueprint))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_draft_one, (i, spec)): i for i, spec in enumerate(blueprint)}
        for fut in as_completed(futures):
            idx, section = fut.result()
            sections[idx] = section

    return [s for s in sections if s is not None]


def _build_fallback_sections(
    *,
    topic: str,
    language: str,
    references: list[dict[str, Any]],
    project_context: dict[str, Any] | None,
    blueprint: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    has_project_context = bool(project_context)
    has_result_evidence = _has_result_evidence(project_context)
    for spec in blueprint:
        section_title = str(spec["title"])
        section_points = [str(point) for point in spec["points"]]
        project_brief = _build_curated_project_brief(
            project_context,
            language,
            topic=topic,
            section_title=section_title,
            section_points=section_points,
        )
        reference_brief = _build_reference_brief(
            references,
            topic=topic,
            section_title=section_title,
            section_points=section_points,
            language=language,
        )
        refs = [line.split(". ", 1)[1].split(" (", 1)[0] for line in reference_brief.splitlines()[:3] if ". " in line]
        paragraphs: list[str] = []
        if language == 'zh':
            paragraphs.append(_zh_fallback_section_intro(topic, section_title))
            for point in section_points:
                paragraphs.append(f"### {point}")
                paragraphs.extend(
                    _zh_fallback_point_paragraphs(
                        topic=topic,
                        point=str(point),
                        section_title=section_title,
                        project_brief=project_brief,
                        refs=refs,
                        has_project_context=has_project_context,
                        has_result_evidence=has_result_evidence,
                    )
                )
            if _detect_section_role(section_title, section_points) == "experiment" and not has_result_evidence:
                paragraphs.append(_missing_experiment_evidence_notice(language, detailed=True))
        else:
            paragraphs.append(f"This section discusses {section_title} for the topic {topic}, keeping the argument focused on motivation, implementation logic, and evidence-backed analysis.")
            for point in section_points:
                paragraphs.append(f"### {point}")
                paragraphs.append(_fallback_point_focus_paragraph(topic=topic, point=point, section_title=section_title, language="en"))
                if _detect_section_role(section_title, [point]) == "experiment" and not has_result_evidence:
                    paragraphs.append(_fallback_experiment_slot_paragraph(topic, point, "en"))
                else:
                    paragraphs.append(f"The current project evidence should remain the anchor for the description: {project_brief}. The text should therefore stay close to real modules, interfaces, execution flow, and engineering trade-offs.")
                paragraphs.append(
                    f"Candidate references such as {', '.join(refs)} can be used to position prior work and justify why this study adopts its current implementation route."
                    if refs
                    else "Core references still need to be added, but the subsection structure already follows a submission-oriented academic narrative."
                )
            if _detect_section_role(section_title, section_points) == "experiment" and not has_result_evidence:
                paragraphs.append(_missing_experiment_evidence_notice(language, detailed=True))
        sections.append({"title": section_title, "content": paragraphs})
    return sections


SECTION_REVIEW_REWRITE_THRESHOLD = 78
SECTION_REVIEW_PASS_THRESHOLD = 84
SECTION_REVIEW_ITEM_LIMIT = 4


def _score_figure_plan_item(
    item: dict[str, Any],
    *,
    keywords: list[str],
    role: str,
) -> tuple[int, int]:
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("section", "figure_type", "goal", "evidence", "caption", "priority")
    )
    score = _keyword_overlap_score(haystack, keywords)
    lowered = haystack.lower()
    if role == "experiment" and any(token in lowered for token in ("result", "analysis", "metric", "compare", "experiment")):
        score += 3
    if role in {"design", "implementation"} and any(token in lowered for token in ("design", "method", "module", "architecture", "workflow")):
        score += 2
    if str(item.get("priority") or "").lower() == "high":
        score += 1
    return score, len(haystack)


def _build_section_figure_slot_brief(
    *,
    topic: str,
    language: str,
    section_title: str,
    section_points: list[str],
    project_context: dict[str, Any] | None,
    limit: int = 2,
) -> str:
    if not project_context:
        return ""
    plan = project_context.get("figure_plan") or []
    if not isinstance(plan, list) or not plan:
        return ""

    role = _detect_section_role(section_title, section_points)
    keywords = _section_keywords(topic, section_title, section_points)
    scored: list[tuple[int, int, int, dict[str, Any]]] = []
    for index, raw_item in enumerate(plan):
        if not isinstance(raw_item, dict):
            continue
        score, length_bonus = _score_figure_plan_item(raw_item, keywords=keywords, role=role)
        if score <= 0 and index >= limit:
            continue
        scored.append((score, length_bonus, index, raw_item))

    if not scored:
        scored = [(0, 0, index, item) for index, item in enumerate(plan[:limit]) if isinstance(item, dict)]

    ranked = sorted(scored, key=lambda entry: (-entry[0], -entry[1], entry[2]))[:limit]
    lines: list[str] = []
    for slot_index, (_, _, _, item) in enumerate(ranked, start=1):
        caption = str(item.get("caption") or "").strip() or (f"Figure slot {slot_index}" if language != "zh" else f"鍥捐〃妲戒綅{slot_index}")
        goal = str(item.get("goal") or "").strip()
        figure_type = str(item.get("figure_type") or "").strip()
        existing_asset = str(item.get("existing_asset") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        line = f"{slot_index}. {caption}"
        if figure_type:
            line += f" | type: {figure_type}"
        if goal:
            line += f" | purpose: {goal}"
        if evidence:
            line += f" | evidence: {evidence}"
        if existing_asset:
            line += f" | existing asset: {existing_asset}"
        lines.append(line)
    return "\n".join(lines)


def _build_section_table_slot_brief(
    *,
    topic: str,
    language: str,
    section_title: str,
    section_points: list[str],
    project_context: dict[str, Any] | None,
    limit: int = 2,
) -> str:
    if not project_context:
        return ""
    plan = project_context.get("table_plan") or []
    if not isinstance(plan, list) or not plan:
        return ""

    role = _detect_section_role(section_title, section_points)
    keywords = _section_keywords(topic, section_title, section_points)
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for item in plan:
        if not isinstance(item, dict):
            continue
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("section", "caption", "goal", "path", "source", "metrics")
        )
        score = _keyword_overlap_score(haystack, keywords)
        lowered = haystack.lower()
        if role == "experiment" and any(token in lowered for token in ("metric", "result", "benchmark", "table", "指标", "结果")):
            score += 3
        if role in {"theory", "design"} and any(token in lowered for token in ("notation", "symbol", "公式", "符号")):
            score += 2
        scored.append((score, len(haystack), item))

    ranked = [item for _score, _length, item in sorted(scored, key=lambda entry: (-entry[0], -entry[1]))[:limit]]
    lines: list[str] = []
    for slot_index, item in enumerate(ranked, start=1):
        caption = str(item.get("caption") or "").strip() or (f"Table slot {slot_index}" if language != "zh" else f"表格槽位{slot_index}")
        goal = str(item.get("goal") or "").strip()
        headers = ", ".join(str(header) for header in (item.get("headers") or [])[:6])
        path = str(item.get("path") or "").strip()
        line = f"{slot_index}. {caption}"
        if goal:
            line += f" | purpose: {goal}"
        if headers:
            line += f" | headers: {headers}"
        if path:
            line += f" | source: {path}"
        lines.append(line)
    return "\n".join(lines)


def _build_section_equation_brief(
    *,
    topic: str,
    section_title: str,
    section_points: list[str],
    project_context: dict[str, Any] | None,
    limit: int = 2,
) -> str:
    if not project_context:
        return ""
    equation_plan = [item for item in (project_context.get("equation_plan") or []) if isinstance(item, dict)]
    if not equation_plan:
        return ""
    keywords = _section_keywords(topic, section_title, section_points)
    scored = []
    for item in equation_plan:
        haystack = " ".join(str(item.get(key) or "") for key in ("section", "focus", "goal", "source"))
        scored.append((_keyword_overlap_score(haystack, keywords), len(haystack), item))
    ranked = [item for _score, _length, item in sorted(scored, key=lambda entry: (-entry[0], -entry[1]))[:limit]]
    return "\n".join(
        f"{index}. {str(item.get('goal') or '').strip()}"
        for index, item in enumerate(ranked, start=1)
        if str(item.get("goal") or "").strip()
    )


def _build_section_evidence_packet(
    *,
    topic: str,
    language: str,
    section_title: str,
    section_points: list[str],
    project_context: dict[str, Any] | None,
    project_brief: str,
    reference_brief: str,
) -> str:
    figure_slots = _build_section_figure_slot_brief(
        topic=topic,
        language=language,
        section_title=section_title,
        section_points=section_points,
        project_context=project_context,
    )
    table_slots = _build_section_table_slot_brief(
        topic=topic,
        language=language,
        section_title=section_title,
        section_points=section_points,
        project_context=project_context,
    )
    equation_slots = _build_section_equation_brief(
        topic=topic,
        section_title=section_title,
        section_points=section_points,
        project_context=project_context,
    )
    chunks: list[str] = []
    if figure_slots:
        heading = "鍥捐〃閿氱偣" if language == "zh" else "Figure/table anchors"
        chunks.append(f"{heading}:\n{figure_slots}")
    if table_slots:
        heading = "琛ㄦ牸閿氱偣" if language == "zh" else "Table anchors"
        chunks.append(f"{heading}:\n{table_slots}")
    if equation_slots:
        heading = "鍏紡涓庣鍙峰畨鎺?" if language == "zh" else "Equation and notation slots"
        chunks.append(f"{heading}:\n{equation_slots}")
    if project_brief:
        heading = "椤圭洰璇佹嵁" if language == "zh" else "Project evidence"
        chunks.append(f"{heading}:\n{project_brief}")
    if reference_brief:
        heading = "参考文献线索" if language == "zh" else "Reference cues"
        chunks.append(f"{heading}:\n{reference_brief}")
    return "\n\n".join(chunk for chunk in chunks if chunk.strip())


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = stripped[start : end + 1]
    try:
        payload = json.loads(candidate)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_review_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:SECTION_REVIEW_ITEM_LIMIT]
    if isinstance(value, str):
        parts = re.split(r"[\n;]+", value)
        return [part.strip(" -*") for part in parts if part.strip(" -*")][:SECTION_REVIEW_ITEM_LIMIT]
    return []


def _parse_section_review(text: str) -> dict[str, Any]:
    payload = _extract_json_object(text) or {}
    raw_score = payload.get("score", 0)
    try:
        score = max(0, min(100, int(raw_score)))
    except Exception:
        score = 0
    return {
        "score": score,
        "issues": _normalize_review_items(payload.get("issues")),
        "missing_evidence": _normalize_review_items(payload.get("missing_evidence")),
        "rewrite_plan": _normalize_review_items(payload.get("rewrite_plan")),
        "preserve": _normalize_review_items(payload.get("preserve")),
        "figure_actions": _normalize_review_items(payload.get("figure_actions")),
    }


def _build_section_review_prompt(
    *,
    topic: str,
    language: str,
    section_title: str,
    section_notes: str,
    evidence_packet: str,
    current_text: str,
) -> str:
    review_language = "Chinese" if language == "zh" else "English"
    return dedent_text(
        f"""
        Review the following thesis section before revision.
        Respond with JSON only using this schema:
        {{
          "score": 0,
          "issues": ["..."],
          "missing_evidence": ["..."],
          "rewrite_plan": ["..."],
          "preserve": ["..."],
          "figure_actions": ["..."]
        }}

        Evaluation rubric:
        - Is the section tightly focused on the section title and topic?
        - Are claims grounded in available project evidence, figures, tables, logs, or references?
        - Does the prose include concrete engineering detail instead of generic filler?
        - Are paragraph openings and transitions varied rather than repetitive?
        - Are figure/table slots used as anchors for results or analysis when relevant?

        Constraints:
        - Output language for JSON strings: {review_language}.
        - Keep each list to at most {SECTION_REVIEW_ITEM_LIMIT} short items.
        - Give a high score only if the section is already evidence-grounded and non-generic.

        Topic: {topic}
        Section title: {section_title}

        Section guardrails:
        {section_notes}

        Evidence packet:
        {evidence_packet}

        Current section:
        {current_text}
        """
    )


def _render_section_review(review: dict[str, Any] | None) -> str:
    if not review:
        return ""
    score = int(review.get("score") or 0)
    issues = list(review.get("issues") or [])
    missing_evidence = list(review.get("missing_evidence") or [])
    rewrite_plan = list(review.get("rewrite_plan") or [])
    preserve = list(review.get("preserve") or [])
    figure_actions = list(review.get("figure_actions") or [])
    if not any((issues, missing_evidence, rewrite_plan, preserve, figure_actions)) and score <= 0:
        return ""

    lines = [f"- Review score: {score}/100"]
    if issues:
        lines.append("- Fix these issues first: " + "; ".join(issues))
    if missing_evidence:
        lines.append("- Add or acknowledge missing evidence: " + "; ".join(missing_evidence))
    if rewrite_plan:
        lines.append("- Rewrite plan: " + "; ".join(rewrite_plan))
    if preserve:
        lines.append("- Preserve these strengths: " + "; ".join(preserve))
    if figure_actions:
        lines.append("- Figure/table actions: " + "; ".join(figure_actions))
    return "\n".join(lines)


def _reference_score(reference: dict[str, Any], *, keywords: list[str], role: str) -> tuple[int, int]:
    title = str(reference.get("title") or "")
    body = " ".join(
        str(value)
        for value in [
            reference.get("title", ""),
            reference.get("abstract", ""),
            reference.get("content_excerpt", ""),
            reference.get("venue", ""),
            reference.get("discipline", ""),
        ]
    )
    score = _keyword_overlap_score(title, keywords) * 3 + _keyword_overlap_score(body, keywords)
    lowered = body.lower()
    if role == "experiment" and any(hint in lowered for hint in ("experiment", "evaluation", "benchmark", "metric", "dataset", "ablation")):
        score += 4
    if role in {"design", "implementation"} and any(hint in lowered for hint in ("system", "architecture", "framework", "implementation", "module")):
        score += 2
    try:
        year_bonus = max(0, min(int(reference.get("year") or 0), 2100) - 2015)
    except Exception:
        year_bonus = 0
    return score, year_bonus


def _build_reference_brief(
    references: list[dict[str, Any]],
    *,
    topic: str = "",
    section_title: str = "",
    section_points: list[str] | None = None,
    language: str = "en",
) -> str:
    if not references:
        return "当前还没有可直接依赖的高质量参考文献。" if language == 'zh' else "No strong indexed references are currently available."
    section_points = section_points or []
    role = _detect_section_role(section_title, section_points)
    keywords = _section_keywords(topic, section_title, section_points)
    scored_references = [
        (*_reference_score(item, keywords=keywords, role=role), index, item)
        for index, item in enumerate(references)
    ]
    ranked = sorted(scored_references, key=lambda entry: (-entry[0], -entry[1], entry[2]))

    lines: list[str] = []
    for index, (_, _, _, item) in enumerate(ranked[:6], start=1):
        title = str(item.get("title") or "Untitled")
        year = str(item.get("year") or "").strip()
        venue = str(item.get("venue") or "").strip()
        abstract = str(item.get("content_excerpt") or item.get("abstract") or "").strip()
        abstract = re.sub(r"\s+", " ", abstract)
        if len(abstract) > 180:
            abstract = abstract[:177].rstrip() + "..."
        line = f"{index}. {title}"
        if year:
            line += f" ({year})"
        if venue:
            line += f", {venue}"
        if abstract:
            line += f": {abstract}"
        lines.append(line)
    return "\n".join(lines)


def _build_project_brief(
    project_context: dict[str, Any] | None,
    language: str,
    *,
    topic: str = "",
    section_title: str = "",
    section_points: list[str] | None = None,
) -> str:
    if not project_context:
        if language == 'zh':
            return "当前尚未提供可直接引用的项目实现证据，后续应补充代码结构、接口说明、运行流程或实验记录。"
        return "No direct project evidence was supplied."

    section_points = section_points or []
    role = _detect_section_role(section_title, section_points)
    keywords = _section_keywords(topic, section_title, section_points)
    summary = str(project_context.get("project_summary") or "").strip()
    project_name = str(project_context.get("project_name") or project_context.get("source_project_path") or "project")
    stack = [str(item).strip() for item in (project_context.get("stack") or []) if str(item).strip()]
    source_files = _select_relevant_items(
        list(project_context.get("candidate_source_files") or []),
        keywords=keywords,
        role=role,
        limit=4,
    )
    config_files = _select_relevant_items(
        list(project_context.get("candidate_config_files") or []),
        keywords=keywords,
        role=role,
        limit=3,
    )
    method_clues = _select_relevant_items(
        list(project_context.get("method_clues") or []),
        keywords=keywords,
        role=role,
        limit=4,
    )
    result_clues = _select_relevant_items(
        list(project_context.get("result_clues") or []),
        keywords=keywords,
        role="experiment",
        limit=4,
    )
    result_files = _select_relevant_items(
        list(project_context.get("candidate_result_files") or []),
        keywords=keywords,
        role="experiment",
        limit=3,
    )
    code_snippets = [str(item).strip() for item in (project_context.get("code_snippets") or []) if str(item).strip()]

    if language == 'zh':
        parts = [f"项目：{project_name}。"]
        if summary:
            parts.append(f"项目概述：{summary}")
        if stack:
            parts.append(f"技术栈：{'、'.join(stack)}")
        if role in {"background", "design", "implementation", "general"} and source_files:
            parts.append(f"关键源码：{', '.join(source_files)}")
        if role in {"design", "implementation", "general"} and config_files:
            parts.append(f"关键配置：{', '.join(config_files)}")
        if role in {"background", "design", "implementation", "general"} and method_clues:
            parts.append(f"方法线索：{'；'.join(method_clues)}")
        if code_snippets and role in {"background", "design", "implementation", "general", "experiment"}:
            parts.append("核心源码片段：\n" + "\n".join(code_snippets[:3]))
        if role == "experiment" and summary:
            parts.append(f"项目功能概述（用于推断实验结果）：{summary}")
        if role == "experiment" and method_clues:
            parts.append(f"核心方法：{'；'.join(method_clues)}")
        if role in {"experiment", "conclusion", "general"} and result_clues:
            parts.append(f"结果线索：{'；'.join(result_clues)}")
        if role in {"experiment", "conclusion", "general"} and result_files:
            parts.append(f"候选结果文件：{', '.join(result_files)}")
        if role == "experiment" and (result_clues or result_files):
            parts.append("请在结果分析中为每个数据表格补充对应的图表槽位，如柱状图、雷达图或曲线图。")
        if role == "experiment" and not result_clues and not result_files:
            parts.append(_missing_experiment_evidence_notice(language, detailed=False))
        return " ".join(parts)

    parts = [f"Project: {project_name}."]
    if summary:
        parts.append(f"Summary: {summary}")
    if stack:
        parts.append(f"Stack: {', '.join(stack)}")
    if role in {"background", "design", "implementation", "general"} and source_files:
        parts.append(f"Key files: {', '.join(source_files)}")
    if role in {"design", "implementation", "general"} and config_files:
        parts.append(f"Config files: {', '.join(config_files)}")
    if role in {"background", "design", "implementation", "general"} and method_clues:
        parts.append(f"Method clues: {'; '.join(method_clues)}")
    if code_snippets and role in {"background", "design", "implementation", "general", "experiment"}:
        parts.append("Core code snippets:\n" + "\n".join(code_snippets[:3]))
    if role in {"experiment", "conclusion", "general"} and result_clues:
        parts.append(f"Result clues: {'; '.join(result_clues)}")
    if role in {"experiment", "conclusion", "general"} and result_files:
        parts.append(f"Candidate result files: {', '.join(result_files)}")
    if role == "experiment" and (result_clues or result_files):
        parts.append("Include figure placeholders (bar charts, radar charts, or curves) after each data table to visualize the experimental results.")
    if role == "experiment" and not result_clues and not result_files:
        parts.append(_missing_experiment_evidence_notice(language, detailed=False))
    return " ".join(parts)


def _zh_fallback_section_intro(topic: str, section_title: str) -> str:
    title = re.sub(r"^[0-9.\s]+", "", section_title).strip()
    if any(keyword in title for keyword in ["绪论", "引言", "综述", "背景"]):
        return f'本节围绕“{topic}”的研究背景、问题边界与论文切入点展开，说明该方向为何值得研究，以及现有工作仍存在哪些空缺。'
    if any(keyword in title for keyword in ["相关技术", "理论基础", "基础", "相关工作"]):
        return f'本节用于交代“{topic}”所依赖的关键技术链路和理论基础，并说明这些基础如何支撑后续设计与实现。'
    if any(keyword in title for keyword in ["系统设计", "方法", "模型", "方案", "架构"]):
        return f'本节聚焦“{topic}”的总体方案与关键设计决策，重点说明模块拆分、接口关系和工程约束。'
    if any(keyword in title for keyword in ["实现", "部署", "模块", "流程"]):
        return f'本节贴近真实工程实现，说明“{topic}”如何落到代码、配置、运行流程和模块协作上。'
    if any(keyword in title for keyword in ["实验", "结果", "分析", "评估"]):
        return f'本节重点回答“{topic}”如何被验证，需要把实验条件、对比对象、评价指标与结果解读组织成完整证据链。'
    if any(keyword in title for keyword in ["结论", "总结", "展望"]):
        return f'本节用于收束“{topic}”的主要贡献与局限，并明确后续需要补强的证据与工作方向。'
    return f'本节围绕“{topic}”展开，重点讨论“{title or section_title}”对应的关键问题，并把背景、设计、实现与分析组织成连续论证。'


def _zh_fallback_point_paragraphs(
    *,
    topic: str,
    point: str,
    section_title: str,
    project_brief: str,
    refs: list[str],
    has_project_context: bool,
    has_result_evidence: bool,
) -> list[str]:
    role = _detect_section_role(section_title, [point])
    paragraphs = [
        _fallback_point_focus_paragraph(topic=topic, point=point, section_title=section_title, language="zh"),
    ]

    if role == "experiment" and not has_result_evidence:
        paragraphs.append(_fallback_experiment_slot_paragraph(topic, point, "zh"))
    elif has_project_context:
        paragraphs.append(
            f"结合当前项目证据，可以优先围绕以下线索展开：{project_brief}。叙述应尽量贴近真实模块、配置、执行流程与工程边界，使正文能够与代码结构和实验安排互相印证。"
        )
    else:
        paragraphs.append(
            "当前尚未提供可直接引用的项目实现证据，因此本小节应先冻结接口、数据流、关键步骤与验证口径，再在后续补入代码细节、运行日志或实验记录，避免把推测内容写成既成事实。"
        )

    if refs:
        paragraphs.append(
            f"文献层面可结合 {', '.join(refs)} 等已有工作，交代相关方法的典型做法、适用边界与未解决问题，再据此说明当前实现路线与已有研究的差异化落点。"
        )
    else:
        paragraphs.append(
            "若当前文献支撑仍不充分，本小节至少要说明后续需要补证的外部依据，例如基线方法、评价标准、系统约束或实现假设。"
        )

    return paragraphs


def _normalize_block_text(block: str, language: str) -> str:
    stripped = block.strip()
    if not stripped:
        return ""
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    bullet_lines = [line for line in lines if re.match(r"^[-*•]\s+|^\d+[.)]\s+", line)]
    if bullet_lines and len(bullet_lines) >= max(2, len(lines) - 1):
        cleaned_items = [re.sub(r"^[-*•]\s+|^\d+[.)]\s+", "", line).strip(" -;,.") for line in lines]
        cleaned_items = [item for item in cleaned_items if item]
        if not cleaned_items:
            return ""
        if language == 'zh':
            return "；".join(cleaned_items).rstrip("；。") + "。"
        return "; ".join(cleaned_items).rstrip(";,.") + "."
    return re.sub(r"\s+", " ", stripped)


def _coerce_section_structure(blocks: list[str], section_points: list[str] | None) -> list[str]:
    expected = [str(point).strip() for point in (section_points or []) if str(point).strip()]
    normalized_blocks: list[str] = []
    for block in blocks:
        if block.startswith("#"):
            heading_text = re.sub(r"^#{1,6}\s*", "", block).strip()
            normalized_blocks.append(f"### {heading_text}")
        else:
            normalized_blocks.append(block)

    if expected and not any(block.startswith("### ") for block in normalized_blocks):
        normalized_blocks.insert(0, f"### {expected[0]}")

    max_headings = max(1, min(5, len(expected) or 5))
    trimmed: list[str] = []
    heading_count = 0
    for block in normalized_blocks:
        if block.startswith("### "):
            if heading_count >= max_headings:
                continue
            heading_count += 1
        trimmed.append(block)
    return trimmed


def _generic_sentence_signature(sentence: str, language: str) -> str:
    stripped = sentence.strip()
    if not stripped:
        return ""

    if language == 'zh':
        normalized = re.sub(r"`[^`]+`", "`path`", stripped)
        normalized = re.sub(r'["“].{1,40}?["”]', '"X"', normalized)
        patterns = [
            (r"^本节围绕.+?展开", "zh_section_intro"),
            (r"^.+?这一部分应", "zh_point_focus"),
            (r"^结合当前项目证据", "zh_project_anchor"),
            (r"^当前尚未提供可直接引用的项目实现证据", "zh_missing_evidence"),
            (r"^当前缺少可直接支撑定量结论的结果证据", "zh_missing_result_evidence"),
            (r"^文献层面可结合", "zh_refs_position"),
            (r"^若当前文献支撑仍不充分", "zh_refs_missing"),
            (r"^.+?当前应围绕.+?先明确实验环境", "zh_experiment_slot"),
        ]
    else:
        normalized = re.sub(r"`[^`]+`", "`path`", stripped.lower())
        normalized = re.sub(r"['\"].{1,60}?['\"]", '"x"', normalized)
        patterns = [
            (r"^this section discusses .+? keeping the argument focused", "en_section_intro"),
            (r"^the subsection on .+? should define the problem setting", "en_problem_setting"),
            (r"^the subsection on .+? should explain how .+? is decomposed into modules", "en_design_decomposition"),
            (r"^the subsection on .+? should stay close to code structure", "en_implementation_focus"),
            (r"^the subsection on .+? should focus on evaluation setup", "en_experiment_focus"),
            (r"^the subsection on .+? should synthesize findings", "en_conclusion_focus"),
            (r"^the subsection on .+? should explain its role in the overall study", "en_generic_point"),
            (r"^the current project evidence should remain the anchor for the description", "en_project_anchor"),
            (r"^candidate references such as .+? can be used to position prior work", "en_refs_position"),
            (r"^core references still need to be added", "en_refs_missing"),
            (r"^direct result evidence is currently unavailable", "en_missing_result_evidence"),
            (r"^because project-side result evidence is still missing", "en_missing_result_evidence"),
        ]

    for pattern, signature in patterns:
        if re.search(pattern, normalized):
            return signature
    return ""


def _sentence_has_specific_detail(sentence: str, language: str) -> bool:
    stripped = sentence.strip()
    if not stripped:
        return False

    detail_patterns = (
        r"`[^`]+`",
        r"\b[a-zA-Z0-9_.-]+\.(?:py|ts|tsx|js|jsx|yaml|yml|json|csv|md|log|txt|docx|pptx)\b",
        r"(?:[A-Za-z0-9_.-]+/){1,}[A-Za-z0-9_.-]+",
        r"(?:[A-Za-z]:\\[^\s]+)",
        r"\b\d+(?:\.\d+)?%?\b",
    )
    if any(re.search(pattern, stripped) for pattern in detail_patterns):
        return True

    if language == 'zh':
        return any(keyword in stripped for keyword in ("模块", "接口", "配置", "流程", "日志", "截图", "指标", "基线", "图表", "数据集", "实验环境", "误差", "参数"))
    lowered = stripped.lower()
    return any(keyword in lowered for keyword in ("module", "interface", "config", "pipeline", "runtime", "log", "screenshot", "metric", "baseline", "dataset", "latency", "accuracy", "figure", "table", "benchmark", "parameter"))


def _is_low_information_sentence(sentence: str, language: str, signature: str) -> bool:
    if signature in {
        "zh_section_intro",
        "zh_point_focus",
        "zh_project_anchor",
        "zh_refs_position",
        "zh_refs_missing",
        "en_section_intro",
        "en_problem_setting",
        "en_design_decomposition",
        "en_experiment_focus",
        "en_conclusion_focus",
        "en_generic_point",
        "en_project_anchor",
        "en_refs_position",
        "en_refs_missing",
    }:
        return True
    stripped = sentence.strip().lower()
    if language == 'zh':
        return stripped.startswith("鏈妭鍥寸粫") or stripped.startswith("缁撳悎褰撳墠椤圭洰璇佹嵁")
    return stripped.startswith("this section discusses") or stripped.startswith("the current project evidence should remain the anchor")


def _filter_repetitive_sentences(
    text: str,
    language: str,
    *,
    seen_sentences: set[str] | None = None,
    seen_scaffolds: set[str] | None = None,
) -> str:
    local_sentences = seen_sentences if seen_sentences is not None else set()
    local_scaffolds = seen_scaffolds if seen_scaffolds is not None else set()
    entries: list[dict[str, Any]] = []
    for sentence in _split_sentences(text, language):
        normalized = _normalize_sentence(sentence)
        if not normalized:
            continue
        scaffold_signature = _generic_sentence_signature(sentence, language)
        entries.append(
            {
                "sentence": sentence.strip(),
                "normalized": normalized,
                "signature": scaffold_signature,
                "specific": _sentence_has_specific_detail(sentence, language),
                "low_info": _is_low_information_sentence(sentence, language, scaffold_signature),
            }
        )

    has_strong_content = any(entry["specific"] or not entry["low_info"] for entry in entries)
    preserved_low_info_signatures = {"zh_missing_evidence", "zh_missing_result_evidence", "zh_experiment_slot", "en_missing_result_evidence"}
    kept: list[str] = []

    for entry in entries:
        normalized = str(entry["normalized"])
        scaffold_signature = str(entry["signature"])
        if normalized in local_sentences:
            continue
        if scaffold_signature and scaffold_signature in local_scaffolds:
            continue
        if entry["low_info"] and has_strong_content and scaffold_signature not in preserved_low_info_signatures:
            continue
        local_sentences.add(normalized)
        if scaffold_signature:
            local_scaffolds.add(scaffold_signature)
        kept.append(str(entry["sentence"]))

    return _join_sentences(kept, language)


def _sanitize_section_output(text: str, title: str, language: str, *, section_points: list[str] | None = None) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^#{1,6}\s+.*?$", "", cleaned, count=1, flags=re.MULTILINE).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    blocks = _split_blocks(cleaned, language)
    sanitized_blocks: list[str] = []
    seen_sentences: set[str] = set()
    seen_scaffolds: set[str] = set()
    for block in blocks:
        if block.startswith("#"):
            sanitized_blocks.append(block)
            continue
        paragraph = _filter_repetitive_sentences(
            block,
            language,
            seen_sentences=seen_sentences,
            seen_scaffolds=seen_scaffolds,
        )
        if paragraph:
            sanitized_blocks.append(_strip_curated_ai_tone(paragraph, language))
    if not sanitized_blocks:
        sanitized_blocks = [title]
    sanitized_blocks = _coerce_section_structure(sanitized_blocks, section_points)
    return "\n\n".join(sanitized_blocks).strip()


def _split_blocks(text: str, language: str) -> list[str]:
    blocks = [
        _normalize_block_text(block, language) if not block.strip().startswith("#") else block.strip()
        for block in re.split(r"\n\s*\n", text)
        if block.strip()
    ]
    return [block for block in blocks if block]


def _split_sentences(text: str, language: str) -> list[str]:
    if language == 'zh':
        pieces = re.split(r"(?<=[。！？；])", text)
    else:
        pieces = re.split(r"(?<=[.!?;])\s+", text)
    return [piece.strip() for piece in pieces if piece.strip()]


def _normalize_sentence(text: str) -> str:
    normalized = re.sub(r"\s+", "", text)
    normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", normalized)
    return normalized.lower()


def _join_sentences(sentences: list[str], language: str) -> str:
    if not sentences:
        return ""
    if language == 'zh':
        return "".join(sentences)
    return " ".join(sentences)


def _strip_ai_tone(text: str, language: str) -> str:
    cleaned = text.strip()
    if language != "zh":
        return re.sub(r"\s+", " ", cleaned)

    rewritten_sentences = [_strip_zh_sentence_prefixes(sentence) for sentence in _split_sentences(cleaned, language)]
    cleaned = "".join(sentence for sentence in rewritten_sentences if sentence)
    for prefix in OPENING_BANLIST_ZH:
        cleaned = re.sub(rf"^{re.escape(prefix)}(?:[:：]\s*)?", "", cleaned).strip()
    return _normalize_zh_spacing(cleaned)


def _normalize_zh_spacing(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    closing_class = re.escape(ZH_CLOSING_PUNCTUATION)
    opening_class = re.escape(ZH_OPENING_PUNCTUATION)
    cleaned = re.sub(rf"\s+([{closing_class}])", r"\1", cleaned)
    cleaned = re.sub(rf"([{opening_class}])\s+", r"\1", cleaned)
    cleaned = re.sub(rf"(?<=[{closing_class}])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = re.sub(rf"(?<=[\u4e00-\u9fff])\s+(?=[{closing_class}])", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _build_curated_section_guardrails(
    *,
    language: str,
    section_title: str,
    section_points: list[str],
    has_project_context: bool,
    has_result_evidence: bool,
    has_reference_support: bool,
) -> str:
    role = _detect_section_role(section_title, section_points)
    heading_lines = [f"### {point}" for point in section_points[:5] if str(point).strip()]

    if language == "zh":
        notes: list[str] = []
        if heading_lines:
            notes.append("尽量保留下列 `###` 小节顺序：" + " / ".join(heading_lines))
        notes.append("段首表述要有变化，不要反复使用模板化总结句。")
        notes.append("优先写场景条件、模块交互、参数取舍、调试现象和误差来源，而不是空泛总结。")
        notes.append("不要把每一段都硬拆成对称的三点结构，也不要在每段末尾补机械式总结句。")
        if role in {"design", "implementation"}:
            notes.append("设计与实现段落要落到模块职责、接口、配置项和调用顺序。")
        if role == "experiment":
            if has_result_evidence:
                notes.append("出现量化结果时，直接解释实验条件、对比对象和原因，不要只写“结果表明”。")
            else:
                notes.append(_missing_experiment_evidence_notice(language, detailed=False))
        if not has_project_context:
            notes.append("项目证据不足时，应把内容写成可验证的工程方案，而不是已经完成的事实。")
        if not has_reference_support:
            notes.append("文献不足时先说明工程边界和取舍，不要虚构文献立场。")
        notes.extend(
            build_profile_guardrails(
                language=language,
                role=role,
                has_result_evidence=has_result_evidence,
                has_reference_support=has_reference_support,
            )
        )
        deduped: list[str] = []
        seen: set[str] = set()
        for note in notes:
            normalized = note.strip()
            if normalized and normalized not in seen:
                deduped.append(normalized)
                seen.add(normalized)
        return "\n".join(f"- {note}" for note in deduped)

    notes = []
    if heading_lines:
        notes.append("Follow these `###` subsection headings in order whenever possible: " + " / ".join(heading_lines))
    notes.append("Vary paragraph openings instead of repeating thesis-shell transitions.")
    notes.append("Prefer scene conditions, module interactions, parameter trade-offs, debugging observations, and error sources over broad summaries.")
    notes.append("Do not force every paragraph into a perfectly balanced three-point structure or a closing wrap-up sentence.")
    if role in {"design", "implementation"}:
        notes.append("Tie the prose to module responsibilities, interfaces, configuration, and execution flow.")
    if role == "experiment":
        if has_result_evidence:
            notes.append("Keep result claims within the available logs, metrics, result files, and explain conditions and causes.")
        else:
            notes.append(_missing_experiment_evidence_notice(language, detailed=False))
    if not has_project_context:
        notes.append("If implementation evidence is missing, frame claims as verifiable design intent rather than completed facts.")
    if not has_reference_support:
        notes.append("If references are sparse, state the engineering boundary clearly and avoid fabricated literature positioning.")
    notes.extend(
        build_profile_guardrails(
            language=language,
            role=role,
            has_result_evidence=has_result_evidence,
            has_reference_support=has_reference_support,
        )
    )
    deduped = []
    seen = set()
    for note in notes:
        normalized = note.strip()
        if normalized and normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)
    return "\n".join(f"- {note}" for note in deduped)


def _build_section_guardrails(
    *,
    language: str,
    section_title: str,
    section_points: list[str],
    has_project_context: bool,
    has_result_evidence: bool,
    has_reference_support: bool,
) -> str:
    return _build_curated_section_guardrails(
        language=language,
        section_title=section_title,
        section_points=section_points,
        has_project_context=has_project_context,
        has_result_evidence=has_result_evidence,
        has_reference_support=has_reference_support,
    )


def _build_curated_project_brief(
    project_context: dict[str, Any] | None,
    language: str,
    *,
    topic: str = "",
    section_title: str = "",
    section_points: list[str] | None = None,
) -> str:
    if not project_context:
        if language == "zh":
            return "当前尚未提供可直接引用的项目实现证据，后续应补充代码结构、接口说明、运行流程或实验记录。"
        return "No direct project evidence was supplied."

    section_points = section_points or []
    role = _detect_section_role(section_title, section_points)
    keywords = _section_keywords(topic, section_title, section_points)
    summary = str(project_context.get("project_summary") or "").strip()
    project_name = str(project_context.get("project_name") or project_context.get("source_project_path") or "project").strip()
    stack = [str(item).strip() for item in (project_context.get("stack") or []) if str(item).strip()]
    source_files = _select_relevant_items(
        list(project_context.get("candidate_source_files") or []),
        keywords=keywords,
        role=role,
        limit=4,
    )
    config_files = _select_relevant_items(
        list(project_context.get("candidate_config_files") or []),
        keywords=keywords,
        role=role,
        limit=3,
    )
    method_clues = _select_relevant_items(
        list(project_context.get("method_clues") or []),
        keywords=keywords,
        role=role,
        limit=4,
    )
    result_clues = _select_relevant_items(
        list(project_context.get("result_clues") or []),
        keywords=keywords,
        role="experiment",
        limit=4,
    )
    result_files = _select_relevant_items(
        list(project_context.get("candidate_result_files") or []),
        keywords=keywords,
        role="experiment",
        limit=3,
    )
    code_snippets = [str(item).strip() for item in (project_context.get("code_snippets") or []) if str(item).strip()]
    figure_plan_summary = str(project_context.get("figure_plan_summary") or "").strip()
    table_plan = [item for item in (project_context.get("table_plan") or []) if isinstance(item, dict)]
    equation_plan = [item for item in (project_context.get("equation_plan") or []) if isinstance(item, dict)]
    variable_inventory = [item for item in (project_context.get("variable_inventory") or []) if isinstance(item, dict)]
    metric_inventory = [str(item).strip() for item in (project_context.get("metric_inventory") or []) if str(item).strip()]

    if language == "zh":
        parts = [f"项目：{project_name}。"]
        if summary:
            parts.append(f"项目概述：{summary}")
        if stack:
            parts.append(f"技术栈：{'、'.join(stack)}")
        if role in {"background", "design", "implementation", "general"} and source_files:
            parts.append(f"关键源码：{', '.join(source_files)}")
        if role in {"design", "implementation", "general"} and config_files:
            parts.append(f"关键配置：{', '.join(config_files)}")
        if role in {"background", "design", "implementation", "general"} and method_clues:
            parts.append(f"方法线索：{'；'.join(method_clues)}")
        if code_snippets and role in {"background", "design", "implementation", "general", "experiment"}:
            parts.append("核心源码片段：\n" + "\n".join(code_snippets[:3]))
        if role in {"experiment", "conclusion", "general"} and result_clues:
            parts.append(f"结果线索：{'；'.join(result_clues)}")
        if role in {"experiment", "conclusion", "general"} and result_files:
            parts.append(f"候选结果文件：{', '.join(result_files)}")
        if figure_plan_summary and role in {"design", "implementation", "experiment", "general", "conclusion"}:
            parts.append(f"图表规划：{figure_plan_summary}")
        if role in {"experiment", "general", "conclusion"} and table_plan:
            parts.append("候选表格：" + "；".join(str(item.get("caption") or "").strip() for item in table_plan[:3] if str(item.get("caption") or "").strip()))
        if role in {"theory", "design", "implementation", "general"} and equation_plan:
            parts.append("公式规划：" + "；".join(str(item.get("goal") or "").strip() for item in equation_plan[:2] if str(item.get("goal") or "").strip()))
        if role in {"theory", "design", "implementation", "general"} and variable_inventory:
            parts.append("主要符号：" + "、".join(f"${str(item.get('symbol') or '').strip()}$" for item in variable_inventory[:6] if str(item.get("symbol") or "").strip()))
        if role in {"experiment", "general", "conclusion"} and metric_inventory:
            parts.append("关键指标：" + "、".join(metric_inventory[:6]))
        if role == "experiment" and not result_clues and not result_files:
            parts.append(_missing_experiment_evidence_notice(language, detailed=False))
        return " ".join(parts)

    parts = [f"Project: {project_name}."]
    if summary:
        parts.append(f"Summary: {summary}")
    if stack:
        parts.append(f"Stack: {', '.join(stack)}")
    if role in {"background", "design", "implementation", "general"} and source_files:
        parts.append(f"Key files: {', '.join(source_files)}")
    if role in {"design", "implementation", "general"} and config_files:
        parts.append(f"Config files: {', '.join(config_files)}")
    if role in {"background", "design", "implementation", "general"} and method_clues:
        parts.append(f"Method clues: {'; '.join(method_clues)}")
    if code_snippets and role in {"background", "design", "implementation", "general", "experiment"}:
        parts.append("Core code snippets:\n" + "\n".join(code_snippets[:3]))
    if role in {"experiment", "conclusion", "general"} and result_clues:
        parts.append(f"Result clues: {'; '.join(result_clues)}")
    if role in {"experiment", "conclusion", "general"} and result_files:
        parts.append(f"Candidate result files: {', '.join(result_files)}")
    if figure_plan_summary and role in {"design", "implementation", "experiment", "general", "conclusion"}:
        parts.append(f"Figure plan: {figure_plan_summary}")
    if role in {"experiment", "general", "conclusion"} and table_plan:
        parts.append("Candidate tables: " + "; ".join(str(item.get("caption") or "").strip() for item in table_plan[:3] if str(item.get("caption") or "").strip()))
    if role in {"theory", "design", "implementation", "general"} and equation_plan:
        parts.append("Equation plan: " + "; ".join(str(item.get("goal") or "").strip() for item in equation_plan[:2] if str(item.get("goal") or "").strip()))
    if role in {"theory", "design", "implementation", "general"} and variable_inventory:
        parts.append("Notation inventory: " + ", ".join(f"${str(item.get('symbol') or '').strip()}$" for item in variable_inventory[:6] if str(item.get("symbol") or "").strip()))
    if role in {"experiment", "general", "conclusion"} and metric_inventory:
        parts.append("Metrics: " + ", ".join(metric_inventory[:6]))
    if role == "experiment" and not result_clues and not result_files:
        parts.append(_missing_experiment_evidence_notice(language, detailed=False))
    return " ".join(parts)


def _strip_curated_ai_tone(text: str, language: str) -> str:
    cleaned = text.strip()
    if language != "zh":
        return re.sub(r"\s+", " ", cleaned)

    rewritten_sentences = [_strip_zh_sentence_prefixes(sentence) for sentence in _split_sentences(cleaned, language)]
    cleaned = "".join(sentence for sentence in rewritten_sentences if sentence)
    for prefix in get_opening_banlist(language):
        cleaned = re.sub(rf"^{re.escape(prefix)}(?:[:：]\s*)?", "", cleaned).strip()
    return _normalize_zh_spacing(cleaned)



def _is_math_heavy_source(project_context: dict[str, Any] | None) -> bool:
    """Detect whether the project source files contain math-heavy patterns (eigen, matrix, optimization, etc.)."""
    if not project_context:
        return False
    math_patterns = [
        "eigen", "matrix", "optimization", "solver", "ceres", "g2o", "factor",
        "quaternion", "transform", "covariance", "kalman", "particle", "mcmc",
        "gradient", "jacobian", "hessian", "cost_function", "residual",
        "pose_graph", "bundle_adjustment", "icp", "ndt", "scan_match",
        "integral", "derivative", "convolution", "fft", "dct", "wavelet",
        "regression", "classifier", "activation", "backpropagation",
        "finite_element", "mesh", "stress", "strain", "modal",
        "modulation", "demodulation", "channel", "equalizer",
        "power_flow", "newton_raphson", "impedance",
        "likelihood", "posterior", "bayesian", "entropy",
        "autoregressive", "spectral", "interpolation",
    ]
    source_files = project_context.get("candidate_source_files") or []
    config_files = project_context.get("candidate_config_files") or []
    all_files = source_files + config_files
    combined = " ".join(str(f).lower() for f in all_files)
    hits = sum(1 for pat in math_patterns if pat in combined)
    return hits >= 3


def _detect_enhancer_domain(topic: str, project_context: dict[str, Any] | None) -> str:
    """Detect project domain for formula point selection. Delegates to shared domain_utils."""
    return detect_domain(topic, project_context)


def _build_algorithm_formula_points(project_context: dict[str, Any] | None) -> list[str]:
    """Build domain-specific formula derivation points for Chapter 2 blueprint."""
    topic = str((project_context or {}).get("project_summary", "") or "")
    domain = _detect_enhancer_domain(topic, project_context)

    common = [
        "核心算法数学建模，从问题定义出发逐步推导核心公式，并用 $$...$$ 展示关键公式。",
        "公式推导保持完整链条：定义 -> 假设 -> 推导 -> 结论，必要时给出编号如 (2.1)。",
        "补充算法伪代码或流程图说明关键步骤与输入输出。",
        "分析算法复杂度、稳定性与适用边界。",
    ]

    domain_points: dict[str, list[str]] = {
        "slam": [
            "环境建模与状态表示，包括位姿、地图和观测量的符号定义。",
            "运动模型与观测模型推导，说明噪声项与状态更新关系。",
            "前端匹配或后端优化目标函数的构建与求解过程。",
            "位姿图优化或滤波更新的核心步骤与收敛条件。",
        ],
        "navigation": [
            "路径规划问题的数学定义，包括状态空间、目标函数与约束。",
            "全局搜索算法代价函数，如 f(n)=g(n)+α·h(n) 的含义与作用。",
            "局部控制的速度空间采样与多目标代价项设计。",
            "代价地图、障碍物膨胀或安全约束的建模过程。",
        ],
        "control": [
            "被控对象的状态空间模型，如 dot{x}=Ax+Bu。",
            "PID 控制律推导与参数物理意义说明。",
            "MPC 等最优控制方法的目标函数与约束设计。",
            "连续系统离散化、稳定性与收敛性分析。",
        ],
        "ml_dl": [
            "模型结构与输入输出形式定义。",
            "损失函数、优化目标与梯度更新公式推导。",
            "反向传播或参数更新机制的关键步骤。",
            "训练策略、正则化和泛化能力分析。",
        ],
        "optimization": [
            "目标函数和约束条件的数学表达。",
            "拉格朗日函数或 KKT 条件推导。",
            "求解器迭代步骤、收敛准则与复杂度分析。",
            "参数敏感性和鲁棒性讨论。",
        ],
    }

    return common + domain_points.get(domain, [])
def _dedupe_sections(sections: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    seen_paragraphs: set[str] = set()
    seen_sentences: set[str] = set()
    seen_scaffolds: set[str] = set()
    rebuilt: list[dict[str, Any]] = []
    for section in sections:
        content = []
        for block in section.get("content", []):
            block_text = str(block).strip()
            if block_text.startswith("#"):
                content.append(block_text)
                continue
            paragraph = _filter_repetitive_sentences(
                block_text,
                language,
                seen_sentences=seen_sentences,
                seen_scaffolds=seen_scaffolds,
            )
            paragraph = _strip_curated_ai_tone(paragraph, language)
            normalized = _normalize_sentence(paragraph)
            if not normalized or normalized in seen_paragraphs:
                continue
            seen_paragraphs.add(normalized)
            content.append(paragraph)
        rebuilt.append({"title": str(section.get("title", "")), "content": content})
    return rebuilt


def _sections_word_count(sections: list[dict[str, Any]], language: str) -> int:
    return sum(_count_words("\n".join(str(item) for item in section.get("content", [])), language) for section in sections)


def _count_words(text: str, language: str) -> int:
    if language == 'zh':
        return len(re.sub(r"\s+", "", text))
    return len(re.findall(r"\b[\w-]+\b", text))


def _trim_block_to_words(text: str, *, language: str, max_words: int) -> str:
    stripped = str(text).strip()
    if not stripped:
        return ""
    if _count_words(stripped, language) <= max_words:
        return stripped

    sentences = _split_sentences(stripped, language)
    kept: list[str] = []
    for sentence in sentences:
        candidate = _join_sentences(kept + [sentence], language)
        if _count_words(candidate, language) > max_words:
            break
        kept.append(sentence)
    if kept:
        return _join_sentences(kept, language).strip()

    if language == 'zh':
        compact = re.sub(r"\s+", "", stripped)
        return compact[:max_words].strip()

    tokens = re.findall(r"\S+\s*", stripped)
    if not tokens:
        return stripped
    kept_tokens: list[str] = []
    running_words = 0
    for token in tokens:
        token_words = _count_words(token, language)
        if token_words == 0 and kept_tokens:
            kept_tokens.append(token)
            continue
        if running_words + token_words > max_words:
            break
        kept_tokens.append(token)
        running_words += token_words
    return "".join(kept_tokens).strip()


def _trim_sections_to_cap(
    sections: list[dict[str, Any]],
    *,
    language: str,
    max_words: int,
) -> tuple[list[dict[str, Any]], int]:
    """Trim content across sections to enforce *max_words* without large undershoot."""
    trimmed: list[dict[str, Any]] = []
    for section in sections:
        content = list(section.get("content", []))
        trimmed.append({"title": section.get("title", ""), "content": content})

    max_rounds = 200  # safety limit
    for _ in range(max_rounds):
        total = _sections_word_count(trimmed, language)
        if total <= max_words:
            break

        excess_words = total - max_words
        # Find the section with the most removable content
        best_idx = -1
        best_block_idx = -1
        best_words = 0
        for i, sec in enumerate(trimmed):
            content = sec.get("content", [])
            # Find last removable (non-heading) paragraph
            for j in range(len(content) - 1, -1, -1):
                if not content[j].startswith("### "):
                    block_words = _count_words(content[j], language)
                    if block_words > best_words and len(content) > 1:
                        best_words = block_words
                        best_idx = i
                        best_block_idx = j
                    break

        if best_idx < 0 or best_block_idx < 0:
            break  # Nothing left to trim

        content = list(trimmed[best_idx].get("content", []))
        current_block = str(content[best_block_idx])
        target_block_words = max(120 if language == 'zh' else 70, best_words - excess_words)
        shortened_block = _trim_block_to_words(
            current_block,
            language=language,
            max_words=target_block_words,
        )
        if shortened_block and shortened_block != current_block:
            content[best_block_idx] = shortened_block
        else:
            content.pop(best_block_idx)
        trimmed[best_idx]["content"] = content

    final_words = _sections_word_count(trimmed, language)
    return trimmed, final_words


def _render_markdown(title: str, sections: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    for section in sections:
        lines.append(f"## {section['title']}")
        lines.append("")
        for block in section.get("content", []):
            lines.append(str(block))
            lines.append("")
    return "\n".join(lines).strip()


def _render_outline(title: str, sections: list[dict[str, Any]], language: str) -> str:
    heading = "论文提纲" if language == 'zh' else "Paper Outline"
    lines = [f"# {heading}：{title}" if language == 'zh' else f"# {heading}: {title}", ""]
    for section in sections:
        lines.append(f"- {section['title']}")
        for block in section.get("content", []):
            if str(block).startswith("### "):
                lines.append(f"  - {str(block)[4:].strip()}")
    return "\n".join(lines).strip()


def _render_plan(title: str, sections: list[dict[str, Any]], language: str, target_words: int, actual_words: int) -> str:
    if language == 'zh':
        lines = [
            f"# 写作计划：{title}",
            "",
            f"- 目标字数：{target_words}",
            f"- 当前正文字数：{actual_words}",
            "- 已执行：结构收敛、章节扩写、重复内容清理、模板化措辞清理",
            "- 建议下一步：补充真实实验数据、图表编号、参考文献编号和导师格式要求",
            "",
            "## 章节检查",
            "",
        ]
        lines.extend(f"- {section['title']}" for section in sections)
        return "\n".join(lines).strip()

    lines = [
        f"# Writing Plan: {title}",
        "",
        f"- Target length: {target_words}",
        f"- Current body length: {actual_words}",
        "- Completed: structure consolidation, section expansion, deduplication, anti-template cleanup",
        "- Next: add real results, figures, citation numbering, and venue-specific formatting",
        "",
        "## Section Checklist",
        "",
    ]
    lines.extend(f"- {section['title']}" for section in sections)
    return "\n".join(lines).strip()


def _build_summary(*, language: str, llm_enabled: bool, target_words: int, actual_words: int) -> str:
    if language == 'zh':
        mode = "已调用大模型多轮扩写与去重" if llm_enabled else "使用本地回退增强链路生成"
        return f"{mode}，当前正文约 {actual_words} 字，目标字数 {target_words}。"
    mode = "Enhanced with multi-pass LLM drafting" if llm_enabled else "Generated with the local fallback enhancement pipeline"
    return f"{mode}. Current body length is about {actual_words}, targeting {target_words}."


def dedent_text(text: str) -> str:
    lines = [line.rstrip() for line in text.strip().splitlines()]
    stripped = [line for line in lines if line]
    if not stripped:
        return ""
    indent = min(len(line) - len(line.lstrip()) for line in stripped)
    return "\n".join(line[indent:] if len(line) >= indent else line for line in lines).strip()


def _expand_short_sections(
    *,
    llm_config: dict[str, str] | list[dict[str, str]],
    topic: str,
    language: str,
    references: list[dict[str, Any]],
    project_context: dict[str, Any] | None,
    sections: list[dict[str, Any]],
    target_words: int,
    remaining_gap: int | None = None,
    target_max_words: int | None = None,
    blueprint: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if isinstance(llm_config, dict):
        _config_chain = [llm_config]
    else:
        _config_chain = list(llm_config)

    _working_config: dict[str, str] | None = None

    def _try_call(prompt: str) -> str:
        nonlocal _working_config
        if _working_config:
            try:
                return _call_model(_working_config, prompt, language)
            except Exception:
                _working_config = None
        last_err = None
        for cfg in _config_chain:
            try:
                result = _call_model(cfg, prompt, language)
                _working_config = cfg
                return result
            except Exception as exc:
                last_err = exc
        raise last_err or RuntimeError("No LLM config available")

    if not sections:
        return []

    total_words = max(_sections_word_count(sections, language), 1)
    remaining_gap = max(remaining_gap if remaining_gap is not None else (target_words - total_words), 0)
    if remaining_gap <= 0:
        return sections

    default_share = 1 / max(len(sections), 1)
    final_fill_threshold = _final_fill_threshold(target_words, language)

    section_meta: list[dict[str, Any]] = []
    raw_shares: list[float] = []
    for sec_idx, section in enumerate(sections):
        section_title = str(section.get("title", ""))
        section_points = [str(item)[4:].strip() for item in section.get("content", []) if str(item).startswith("### ")][:5]
        current_text = "\n\n".join(str(item) for item in section.get("content", []))
        current_words = _count_words(current_text, language)
        spec = blueprint[sec_idx] if blueprint and sec_idx < len(blueprint) else {}
        try:
            section_share = float(spec.get("share", default_share))
        except (TypeError, ValueError, AttributeError):
            section_share = default_share
        section_role = _detect_section_role(section_title, section_points)
        minimum_share = 0.05 if len(sections) >= 6 else default_share * 0.55
        if section_role == "conclusion":
            section_share = min(section_share, 0.10 if language == 'zh' else 0.08)
        section_share = max(minimum_share, section_share)
        raw_shares.append(section_share)
        section_meta.append(
            {
                "sec_idx": sec_idx,
                "section": section,
                "section_title": section_title,
                "section_points": section_points,
                "current_text": current_text,
                "current_words": current_words,
                "section_role": section_role,
            }
        )

    share_total = sum(raw_shares) or 1.0
    normalized_shares = [share / share_total for share in raw_shares]
    allocations = [0] * len(section_meta)

    if remaining_gap <= final_fill_threshold:
        best_pos = 0
        best_score: tuple[float, int, int] | None = None
        for pos, meta in enumerate(section_meta):
            share = normalized_shares[pos]
            role_penalty = -1 if meta["section_role"] == "conclusion" else 0
            score = (role_penalty + share, meta["current_words"], -pos)
            if best_score is None or score > best_score:
                best_score = score
                best_pos = pos
        allocations[best_pos] = remaining_gap
    else:
        raw_allocations = [remaining_gap * share for share in normalized_shares]
        allocations = [int(value) for value in raw_allocations]
        assigned = sum(allocations)
        if assigned < remaining_gap:
            remainder_order = sorted(
                range(len(raw_allocations)),
                key=lambda idx: raw_allocations[idx] - allocations[idx],
                reverse=True,
            )
            for idx in remainder_order:
                if assigned >= remaining_gap:
                    break
                allocations[idx] += 1
                assigned += 1

    skip_indices: list[int] = []
    expand_jobs: list[tuple[int, dict[str, Any], str, list[str], str, int, int]] = []
    for pos, meta in enumerate(section_meta):
        allocation = max(0, int(allocations[pos]))
        current_words = int(meta["current_words"])
        desired_words = current_words + allocation
        if meta["section_role"] == "conclusion":
            conclusion_cap = min(
                max(1200 if language == 'zh' else 650, int(target_words * (0.08 if language == 'zh' else 0.09))),
                2600 if language == 'zh' else 1400,
            )
            desired_words = min(desired_words, conclusion_cap)
        if target_max_words is not None:
            desired_words = min(desired_words, target_max_words)
        if desired_words <= current_words:
            skip_indices.append(meta["sec_idx"])
            continue
        expand_jobs.append(
            (
                meta["sec_idx"],
                meta["section"],
                meta["current_text"],
                meta["section_points"],
                meta["section_title"],
                current_words,
                desired_words,
            )
        )

    def _expand_one(job: tuple[int, dict[str, Any], str, list[str], str, int, int]) -> tuple[int, dict[str, Any]]:
        sec_idx, section, current_text, section_points, section_title, current_words, desired_words = job
        try:
            from sidecar.routers.writing import _section_progress_callback
            _section_progress_callback(sec_idx, len(sections), section_title, "expanding")
        except Exception:
            pass
        project_brief = _build_curated_project_brief(
            project_context, language, topic=topic, section_title=section_title, section_points=section_points,
        )
        reference_brief = _build_reference_brief(
            references, topic=topic, section_title=section_title, section_points=section_points, language=language,
        )
        section_notes = _build_curated_section_guardrails(
            language=language, section_title=section_title, section_points=section_points,
            has_project_context=bool(project_context),
            has_result_evidence=_has_result_evidence(project_context),
            has_reference_support=bool(references),
        )
        evidence_packet = _build_section_evidence_packet(
            topic=topic,
            language=language,
            section_title=section_title,
            section_points=section_points,
            project_context=project_context,
            project_brief=project_brief,
            reference_brief=reference_brief,
        )
        review = _parse_section_review(
            _try_call(
                _build_section_review_prompt(
                    topic=topic,
                    language=language,
                    section_title=section_title,
                    section_notes=section_notes,
                    evidence_packet=evidence_packet,
                    current_text=current_text,
                )
            )
        )
        prompt = _build_expand_prompt(
            topic=topic, language=language, section_title=section_title,
            current_words=current_words, desired_words=desired_words,
            minimum_words=desired_words,
            section_notes=section_notes,
            evidence_packet=evidence_packet,
            current_text=current_text,
            review=review,
        )
        expanded = _try_call(prompt)
        return sec_idx, {
            "title": section_title,
            "content": _split_blocks(_sanitize_section_output(expanded, section_title, language, section_points=section_points), language),
        }

    rebuilt: list[dict[str, Any] | None] = [None] * len(sections)
    for i in skip_indices:
        rebuilt[i] = sections[i]

    if expand_jobs:
        max_workers = min(3, len(expand_jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_expand_one, job): job[0] for job in expand_jobs}
            for fut in as_completed(futures):
                idx, section = fut.result()
                rebuilt[idx] = section

    return [s for s in rebuilt if s is not None]


def _build_section_prompt(
    *,
    topic: str,
    language: str,
    paper_type: str,
    section_title: str,
    section_points: list[str],
    section_target: int,
    evidence_packet: str,
    section_notes: str,
) -> str:
    heading_contract = "\n".join(f"- ### {point}" for point in section_points[:5] if str(point).strip())
    if language == "zh":
        return dedent_text(
            f"""
            你是严格的中文工科论文写作助手。请直接输出可放入论文正文的章节内容，不要输出写作说明、提纲解释或元话语。

            论文主题：{topic}
            论文类型：{paper_type}
            章节标题：{section_title}
            建议覆盖的小节：{', '.join(section_points)}
            目标字数：约 {section_target} 字

            写作要求：
            - 只输出 Markdown 正文。
            - 保持 3 到 5 个 `###` 小节即可，不要拆得过碎。
            - 尽量沿用以下小节标题并保持顺序：
            {heading_contract or '- ### 保持章节结构紧凑'}
            - 不要编造不存在的数据、图表编号、引用或已实现功能。
            - 论证必须优先贴近项目证据、图表槽位、日志、结果文件和参考文献。
            - 涉及变量时统一使用 `$...$` 行内公式格式；独立公式使用 `$$...$$` 并给出类似“式2.1”的章节编号。
            - 如果证据包里有表格槽位或符号表，应在对应段落附近显式落表，不要只在章末集中罗列。
            - 多写工程约束、模块协作、参数取舍、异常现象和原因解释，少写空泛总结。
            - 段首和过渡语要自然变化，避免连续使用模板化结论句。

            章节护栏：
            {section_notes or '- 保持论证贴近章节目标与可用证据。'}

            证据包：
            {evidence_packet or '当前暂无可直接引用的项目证据，请明确写出边界，避免把推测写成既成事实。'}
            """
        )

    return dedent_text(
        f"""
        Write a polished academic thesis section in English.
        Topic: {topic}
        Paper type: {paper_type}
        Section title: {section_title}
        Suggested subsection coverage: {', '.join(section_points)}
        Target length: around {section_target} words

        Requirements:
        - Output only the section body in Markdown.
        - Use at most 3 to 5 `###` subsections.
        - Prefer these subsection headings and keep the order when possible:
        {heading_contract or '- ### Keep the subsection layout compact'}
        - Do not fabricate data, figure numbers, citations, or completed features.
        - Keep the prose evidence-first and engineering-heavy rather than generic.
        - Use figure/table anchors when relevant and explain what each result supports.
        - Render variables in inline math using `$...$`; render standalone equations with `$$...$$` and chapter-aware labels such as Eq. (2.1).
        - When table or notation slots are provided, place them near the supporting discussion instead of listing them only at the end of the chapter.
        - Vary openings and transitions instead of repeating stock conclusion phrases.

        Section guardrails:
        {section_notes or '- Keep the prose aligned with section intent and available evidence.'}

        Evidence packet:
        {evidence_packet or 'No strong direct evidence is available. State boundaries clearly and avoid turning speculation into fact.'}
        """
    )


def _build_expand_prompt(
    *,
    topic: str,
    language: str,
    section_title: str,
    current_words: int,
    desired_words: int,
    minimum_words: int,
    section_notes: str,
    evidence_packet: str,
    current_text: str,
    review: dict[str, Any] | None = None,
) -> str:
    minimum_addition = max(0, minimum_words - current_words)
    review_summary = _render_section_review(review)
    if language == "zh":
        return dedent_text(
            f"""
            请在不改变章节标题和核心判断的前提下，重写并扩写下面这段中文论文正文。

            论文主题：{topic}
            章节标题：{section_title}
            当前字数：约 {current_words} 字
            目标字数：约 {desired_words} 字
            最低要求：扩写后不少于 {minimum_words} 字，至少新增 {minimum_addition} 字的实质内容

            扩写要求：
            - 保留现有 `###` 结构和主要论点，不要推翻原有章节逻辑。
            - 优先修复审稿问题，再补充工程细节、证据解释、条件边界和原因分析。
            - 不要虚构精确指标、图表编号、引用或实验结论。
            - 如果证据不足，要明确写出限制和待验证点，不要强行写成既成事实。
            - 变量统一使用 `$...$`，独立公式统一使用 `$$...$$`，并给出章节编号。
            - 表格、符号表和图应贴近对应论述段落，不要全部堆到章节末尾。
            - 不要做轻微改写后原样返回，必须显著增加有效信息量。

            章节护栏：
            {section_notes or '- 保持论证贴近章节目标与证据。'}

            审稿意见：
            {review_summary or '- 优先增强证据锚定、段落变化和工程细节。'}

            证据包：
            {evidence_packet or '当前暂无可直接引用的项目证据，请明确边界并避免把推测写成事实。'}

            当前章节：
            {current_text}
            """
        )

    return dedent_text(
        f"""
        Rewrite and expand the following thesis section without changing its title or core claims.
        Topic: {topic}
        Section title: {section_title}
        Current length: about {current_words} words
        Target length: around {desired_words} words
        Minimum requirement: reach at least {minimum_words} words and add at least {minimum_addition} new words of substantive content

        Expansion requirements:
        - Preserve the current `###` structure and the main argument.
        - Fix the review issues first, then deepen the section with engineering detail, evidence interpretation, and constraints.
        - Do not fabricate exact metrics, figure numbers, citations, or completed experiments.
        - If evidence is thin, state the limitation explicitly instead of overstating the claim.
        - Use `$...$` for inline variables, `$$...$$` for display equations, and keep equation numbering chapter-aware.
        - Place tables, notation tables, and figures close to the supporting discussion rather than moving them all to chapter endings.
        - Do not return a lightly rephrased section with roughly the same length.

        Section guardrails:
        {section_notes or '- Keep the prose aligned with section intent and available evidence.'}

        Review feedback:
        {review_summary or '- Strengthen evidence grounding, figure anchors, and paragraph-level specificity.'}

        Evidence packet:
        {evidence_packet or 'No strong direct evidence is available. State boundaries clearly and avoid turning speculation into fact.'}

        Current section:
        {current_text}
        """
    )


def _build_system_prompt(language: str) -> str:
    if language == "zh":
        return (
            "你是严格的中文学术写作助手。优先输出证据驱动、结构清晰、避免模板腔的论文正文。"
            "不要输出元话语、项目清单、提示词解释或营销化措辞。"
            "正文不要使用粗体或斜体，不要伪造引用和数据。"
        )
    return (
        "You are a rigorous academic writing assistant. "
        "Prefer evidence-first, concrete, non-templated prose. "
        "Avoid meta-writing, outline fragments, and fabricated citations or data."
    )


def _call_model(config: dict[str, str], prompt: str, language: str) -> str:
    def _openai_compatible_url(base: str, endpoint: str) -> str:
        endpoint = endpoint.lstrip("/")
        if base.endswith("/" + endpoint):
            return base
        path = urlparse(base).path.rstrip("/")
        if re.search(r"/v\d+(?:beta\d*)?$", path):
            return f"{base}/{endpoint}"
        return f"{base}/v1/{endpoint}"

    def _extract_text_from_sse_body(body: str) -> str:
        text_parts: list[str] = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if data_str in ("", "[DONE]"):
                continue
            try:
                event = json.loads(data_str)
            except Exception:
                if data_str and not data_str.startswith("{"):
                    text_parts.append(data_str)
                continue
            if event.get("type") == "response.output_text.delta":
                delta = str(event.get("delta") or "")
                if delta:
                    text_parts.append(delta)
                continue
            choice = ((event.get("choices") or [{}])[0] or {})
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text_parts.append(str(item.get("text") or ""))
        return "".join(part for part in text_parts if part).strip()

    def _response_json_or_text(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            body = (response.text or "").strip()
            content_type = (response.headers.get("content-type") or "").lower()
            if "text/event-stream" in content_type or body.startswith("data:"):
                extracted = _extract_text_from_sse_body(body)
                if extracted:
                    return {"_raw_text": extracted}
            if body and "text/plain" in content_type and "<html" not in body[:200].lower():
                return {"_raw_text": body}
            preview = body[:180].replace("\n", "\\n") if body else "<empty>"
            raise ValueError(f"Non-JSON response from {response.url} [{response.status_code}]: {preview}") from exc

    def _clean_model_text(value: Any) -> str:
        return sanitize_utf8_text(str(value or "")).strip()

    base_url = config["base_url"]
    provider = config["provider"]
    model = config["model"]
    timeout = (30, 600)
    prompt = sanitize_utf8_text(prompt)
    system_prompt = sanitize_utf8_text(_build_system_prompt(language))
    clean_base = base_url.rstrip("/")
    is_anthropic = (
        provider in {"claude", "anthropic"}
        or "/v1/messages" in clean_base
        or clean_base.endswith("/messages")
        or "anthropic" in clean_base.lower()
    )

    if provider == "ollama" or "/api/chat" in clean_base:
        response = requests.post(
            base_url,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.35},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return _clean_model_text((payload.get("message") or {}).get("content") or payload.get("response") or "")

    if is_anthropic:
        anth_url = clean_base if clean_base.endswith("/v1/messages") else clean_base + "/v1/messages"
        response = requests.post(
            anth_url,
            headers={
                "x-api-key": config["api_key"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = _response_json_or_text(response)
        if isinstance(payload, dict) and payload.get("_raw_text"):
            return _clean_model_text(payload.get("_raw_text") or "")
        content = payload.get("content") or []
        if isinstance(content, list):
            text_parts = [_clean_model_text(item.get("text") or "") for item in content if isinstance(item, dict)]
            return "\n".join(part for part in text_parts if part).strip()
        return ""

    if "/v1/responses" in clean_base or clean_base.endswith("/responses"):
        responses_url = _openai_compatible_url(clean_base, "responses")
        try:
            with requests.post(
                responses_url,
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "instructions": system_prompt,
                    "input": [{"role": "user", "content": prompt}],
                    "stream": True,
                },
                stream=True,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                text_parts: list[str] = []
                for raw_line in response.iter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str in ("", "[DONE]"):
                        continue
                    try:
                        event = json.loads(data_str)
                    except Exception:
                        continue
                    if event.get("type") == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            text_parts.append(sanitize_utf8_text(str(delta)))
                if text_parts:
                    return _clean_model_text("".join(text_parts))
        except OSError as exc:
            if getattr(exc, "errno", None) != 22:
                raise
        response = requests.post(
            responses_url,
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "instructions": system_prompt,
                "input": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = _response_json_or_text(response)
        if isinstance(payload, dict) and payload.get("_raw_text"):
            return _clean_model_text(payload.get("_raw_text") or "")
        output = payload.get("output") or []
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for chunk in item.get("content") or []:
                if isinstance(chunk, dict) and chunk.get("type") == "output_text":
                    text_parts.append(_clean_model_text(chunk.get("text") or ""))
        return "\n".join(part for part in text_parts if part).strip()

    chat_url = _openai_compatible_url(clean_base, "chat/completions")
    response = requests.post(
        chat_url,
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.35,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = _response_json_or_text(response)
    if isinstance(payload, dict) and payload.get("_raw_text"):
        return _clean_model_text(payload.get("_raw_text") or "")
    choice = ((payload.get("choices") or [{}])[0] or {}).get("message") or {}
    content = choice.get("content") or ""
    if isinstance(content, list):
        return "\n".join(_clean_model_text(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
    return _clean_model_text(content)
def _strip_zh_sentence_prefixes(sentence: str) -> str:
    cleaned = sentence.strip()
    if not cleaned:
        return ""

    while True:
        previous = cleaned
        for prefix in OPENING_BANLIST_ZH:
            cleaned = re.sub(rf"^{re.escape(prefix)}(?:[:：]\s*)?", "", cleaned).strip()
        for pattern in ZH_SENTENCE_PREFIX_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned).strip()
        if cleaned == previous:
            break
    return cleaned


