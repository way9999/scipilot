from __future__ import annotations

from pathlib import Path
from typing import Any


WRITING_PROFILE_SOURCES: tuple[dict[str, str], ...] = (
    {
        "name": "Norman-bury/research-writing-skill",
        "url": "https://github.com/Norman-bury/research-writing-skill",
        "focus": "将论文写作拆成研究、提纲、逐章写作和回查的阶段化流程。",
    },
    {
        "name": "LigphiDonk/academic-figure-generator",
        "url": "https://github.com/LigphiDonk/academic-figure-generator",
        "focus": "先理解内容，再输出带视觉规格的学术图表计划。",
    },
    {
        "name": "论文prompt (1).txt",
        "url": r"G:\sci\论文prompt (1).txt",
        "focus": "中文论文写作的去模板化、证据锚定、数字格式与图表约束。",
    },
)

ZH_BANNED_OPENINGS: tuple[str, ...] = (
    "首先",
    "其次",
    "再次",
    "最后",
    "综上所述",
    "总而言之",
    "结果表明",
    "可以看出",
    "由此可见",
    "进一步分析可知",
    "值得注意的是",
    "需要指出的是",
    "一方面",
    "另一方面",
)

ZH_BANNED_HYPE_PHRASES: tuple[str, ...] = (
    "深入探讨",
    "画卷",
    "织锦",
    "挂毯",
    "双刃剑",
    "惊人的",
    "颠覆性",
    "开创性",
    "里程碑",
    "完美识别",
)


def get_opening_banlist(language: str) -> tuple[str, ...]:
    if language == "zh":
        return ZH_BANNED_OPENINGS
    return ()


def build_profile_guardrails(
    *,
    language: str,
    role: str,
    has_result_evidence: bool,
    has_reference_support: bool,
) -> list[str]:
    if language == "zh":
        notes = [
            "正文保持连续段落和 Markdown 标题，不使用加粗、斜体或项目符号堆砌观点。",
            "所有具体数值、百分比、金额、公式编号和图表编号统一使用阿拉伯数字，百分比统一使用 %。",
            "只依据当前项目证据和已提供参考资料写作，不补写不存在的实验值、文献、接口或结论。",
            "避免套话开头，尤其不要重复使用“首先、其次、综上所述、结果表明、可以看出、一方面、另一方面”这类模板句。",
            "语言保持平实克制，不写宣传口吻，不使用“颠覆性、里程碑、惊人的”这类夸张词。",
            "若证据不足，应明确写成待验证的工程判断或后续计划，而不是写成已经完成的事实。",
        ]
        if role in {"design", "implementation"}:
            notes.append("设计与实现段落优先写模块职责、输入输出、配置约束和调用顺序，不先堆教材式定义。")
        if role == "experiment":
            notes.append("实验段落先写实验条件、评价指标、对比对象和数据来源，再解释结果原因、误差来源和局限。")
            notes.append("当已有数据表或图像证据时，正文应直接解释差异来源，而不是再补一层空泛总结。")
        if role == "conclusion":
            notes.append("结论段落只收束贡献、限制和后续工作，不重复铺陈大段实验描述。")
        if not has_result_evidence:
            notes.append("没有真实实验结果时，不伪造精确指标，只保留合理的验证设计和预期观察点。")
        if not has_reference_support:
            notes.append("没有可用文献支撑时，先写清工程边界和设计取舍，不虚构文献立场。")
        return notes

    notes = [
        "Keep the section in continuous prose with Markdown headings only; avoid bold emphasis and bullet-heavy exposition.",
        "Anchor every claim to the supplied project evidence and provided references; never invent metrics, citations, or capabilities.",
        "Keep numbers in Arabic numerals and percentages in the % form.",
        "Avoid stock thesis transitions and summary-first filler; use restrained academic prose instead of hype.",
        "If evidence is incomplete, frame statements as verifiable design intent or planned validation.",
    ]
    if role in {"design", "implementation"}:
        notes.append("Tie the writing to module responsibilities, interfaces, configuration boundaries, and execution order.")
    if role == "experiment":
        notes.append("State setup, baselines, metrics, and evidence source before interpreting results.")
        notes.append("When result evidence exists, explain causes and limitations instead of repeating generic takeaways.")
    if role == "conclusion":
        notes.append("Use the conclusion to compress contributions, limitations, and next steps rather than replaying the full results narrative.")
    if not has_result_evidence:
        notes.append("Without real results, describe validation design and expected observations instead of fabricated numbers.")
    if not has_reference_support:
        notes.append("If literature support is sparse, make the engineering boundary explicit instead of inventing positioning.")
    return notes


def _normalize_list(values: Any) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        item = str(value).strip()
        if item:
            normalized.append(item)
    return normalized


def _pick_matching_item(items: list[str], keywords: tuple[str, ...]) -> str:
    lowered = [(item, item.lower()) for item in items]
    for item, lowered_item in lowered:
        if any(keyword in lowered_item for keyword in keywords):
            return item
    return items[0] if items else ""


def _evidence_snippet(project_context: dict[str, Any], keys: tuple[str, ...], limit: int = 2) -> str:
    snippets: list[str] = []
    for key in keys:
        values = _normalize_list(project_context.get(key))
        if values:
            snippets.extend(values[:limit])
        if len(snippets) >= limit:
            break
    return " ; ".join(snippets[:limit]).strip()


def _figure_prompt(language: str, figure_type: str, goal: str, evidence: str) -> str:
    if language == "zh":
        return (
            f"请生成一张用于学术论文的{figure_type}。"
            f"核心目标是：{goal}。"
            f"必须基于以下证据组织标签与图例：{evidence or '使用项目上下文中的真实模块和结果字段'}。"
            "画面采用白底、扁平化、少量强调色、无 3D 效果、标签完整、适合直接放入论文正文。"
        )
    return (
        f"Generate an academic {figure_type}. "
        f"The figure should communicate: {goal}. "
        f"Use the following evidence to derive labels and legends: {evidence or 'project modules and real result fields from context'}. "
        "Use a clean white background, flat styling, restrained accent colors, no 3D effects, and publication-ready labels."
    )


def _plan_budget_total(project_context: dict[str, Any], key: str, fallback: int) -> int:
    chapter_budget = (project_context or {}).get("chapter_budget") or {}
    budget = chapter_budget.get(key) or {}
    total = sum(int(value) for value in budget.values() if isinstance(value, (int, float)))
    return max(total, fallback)


def _section_title_for_role(role: str, language: str) -> str:
    if language == "zh":
        mapping = {
            "background": "研究背景",
            "theory": "理论基础",
            "design": "系统设计",
            "implementation": "实现细节",
            "experiment": "实验结果",
            "conclusion": "结论与展望",
        }
    else:
        mapping = {
            "background": "Background",
            "theory": "Theory",
            "design": "System Design",
            "implementation": "Implementation",
            "experiment": "Results",
            "conclusion": "Conclusion",
        }
    return mapping.get(role, mapping["design"])


def _figure_type_for_role(role: str, language: str) -> str:
    if language == "zh":
        mapping = {
            "design": "系统架构图",
            "scene": "实验场景图",
            "comparison": "对比分析图",
            "result": "结果曲线图",
            "implementation": "模块流程图",
        }
    else:
        mapping = {
            "design": "system architecture diagram",
            "scene": "experiment setup figure",
            "comparison": "comparison chart",
            "result": "results chart",
            "implementation": "workflow diagram",
        }
    return mapping.get(role, mapping["result"])


def _goal_for_candidate(candidate: dict[str, Any], language: str, project_name: str, fallback_topic: str) -> str:
    caption = str(candidate.get("caption") or "").strip() or project_name
    section = str(candidate.get("section") or "experiment")
    if language == "zh":
        if section == "design":
            return f"说明 {project_name} 的核心结构、模块关系和输入输出，重点围绕 {caption} 展开。"
        if section == "implementation":
            return f"展示 {project_name} 的关键流程、模块协作或运行时链路，围绕 {caption} 展开。"
        return f"围绕 {fallback_topic or project_name} 的结果证据解释 {caption} 所展示的趋势、差异或现象。"
    if section == "design":
        return f"Explain the core structure, module interactions, and interfaces of {project_name} through {caption}."
    if section == "implementation":
        return f"Show the implementation flow, runtime coordination, or pipeline of {project_name} through {caption}."
    return f"Use {caption} to explain the measured trend, contrast, or outcome for {fallback_topic or project_name}."


def build_figure_plan(
    *,
    topic: str,
    language: str,
    project_context: dict[str, Any] | None,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    context = project_context or {}
    figures = _normalize_list(context.get("paper_workspace_figure_files"))
    result_files = _normalize_list(context.get("candidate_result_files"))
    result_clues = _normalize_list(context.get("result_clues"))
    method_clues = _normalize_list(context.get("method_clues"))
    source_files = _normalize_list(context.get("candidate_source_files"))
    config_files = _normalize_list(context.get("candidate_config_files"))
    stack = _normalize_list(context.get("stack"))
    figure_candidates = [item for item in (context.get("figure_candidates") or []) if isinstance(item, dict)]
    dynamic_max_items = _plan_budget_total(context, "figures", max_items)

    plans: list[dict[str, Any]] = []

    def add_plan(
        *,
        figure_id: str,
        section: str,
        figure_type: str,
        goal: str,
        evidence: str,
        keywords: tuple[str, ...],
        priority: str,
    ) -> None:
        if len(plans) >= dynamic_max_items:
            return
        existing_figure = _pick_matching_item(figures + result_files, keywords)
        caption_core = goal.replace("展示", "").replace("说明", "").strip() or goal
        if language == "zh":
            caption = f"图{len(plans) + 1} {caption_core}"
        else:
            caption = f"Figure {len(plans) + 1}. {caption_core}"
        plans.append(
            {
                "id": figure_id,
                "section": section,
                "figure_type": figure_type,
                "goal": goal,
                "evidence": evidence,
                "existing_asset": existing_figure,
                "priority": priority,
                "caption": caption,
                "visual_spec": (
                    "白底、扁平化、少量强调色、图例完整、避免 3D 和过度装饰。"
                    if language == "zh"
                    else "White background, flat academic styling, restrained accent colors, complete legends, no 3D decoration."
                ),
                "generator_prompt": _figure_prompt(language, figure_type, goal, evidence),
            }
        )

    project_name = str(context.get("project_name") or Path(str(context.get("source_project_path") or topic or "project")).name)

    for index, candidate in enumerate(figure_candidates, start=1):
        if len(plans) >= dynamic_max_items:
            break
        section_role = str(candidate.get("section") or "experiment")
        candidate_role = str(candidate.get("role") or "result")
        caption_core = str(candidate.get("caption") or "").strip() or f"Figure candidate {index}"
        evidence_path = str(candidate.get("path") or "").strip()
        existing_asset = evidence_path
        if evidence_path and not evidence_path.startswith("output/figures/"):
            existing_asset = f"output/figures/{Path(evidence_path).name}"
        caption = f"图{len(plans) + 1} {caption_core}" if language == "zh" else f"Figure {len(plans) + 1}. {caption_core}"
        goal = _goal_for_candidate(candidate, language, project_name, topic)
        figure_type = _figure_type_for_role(candidate_role, language)
        plans.append(
            {
                "id": f"figure-{index}",
                "section": _section_title_for_role(section_role, language),
                "figure_type": figure_type,
                "goal": goal,
                "evidence": evidence_path or caption_core,
                "existing_asset": existing_asset,
                "priority": "high" if section_role in {"design", "experiment"} else "medium",
                "caption": caption,
                "visual_spec": (
                    "白底、扁平化、图例完整、直接服务正文论证，不做与证据无关的装饰。"
                    if language == "zh"
                    else "White background, flat academic styling, complete legends, and no decorative elements unrelated to the evidence."
                ),
                "generator_prompt": _figure_prompt(language, figure_type, goal, evidence_path or caption_core),
            }
        )

    if source_files or stack:
        add_plan(
            figure_id="architecture",
            section="系统设计" if language == "zh" else "System Design",
            figure_type="系统架构图" if language == "zh" else "system architecture diagram",
            goal=(
                f"说明 {project_name} 的核心模块、输入输出和主调用链"
                if language == "zh"
                else f"Explain the core modules, inputs, outputs, and main execution path of {project_name}"
            ),
            evidence=_evidence_snippet(context, ("candidate_source_files", "candidate_config_files", "stack"), limit=3),
            keywords=("arch", "architecture", "module", "system", "架构", "模块"),
            priority="high",
        )

    if method_clues:
        add_plan(
            figure_id="method-flow",
            section="方法设计" if language == "zh" else "Method",
            figure_type="方法流程图" if language == "zh" else "method flowchart",
            goal=(
                f"梳理 {topic or project_name} 的主要处理流程、关键决策点和输出结果"
                if language == "zh"
                else f"Map the main processing flow, decision points, and outputs for {topic or project_name}"
            ),
            evidence=_evidence_snippet(context, ("method_clues", "candidate_source_files"), limit=3),
            keywords=("flow", "pipeline", "process", "workflow", "流程", "方法"),
            priority="high",
        )

    if result_clues or result_files:
        add_plan(
            figure_id="results-overview",
            section="实验结果" if language == "zh" else "Results",
            figure_type="结果对比图" if language == "zh" else "results comparison chart",
            goal=(
                "展示核心实验指标、对比对象和总体趋势"
                if language == "zh"
                else "Show the core metrics, baselines, and overall performance trend"
            ),
            evidence=_evidence_snippet(context, ("result_clues", "candidate_result_files"), limit=3),
            keywords=("result", "metric", "compare", "bar", "curve", "结果", "指标", "对比"),
            priority="high",
        )

    if len(result_clues) >= 2 or len(result_files) >= 2:
        add_plan(
            figure_id="ablation-or-breakdown",
            section="结果分析" if language == "zh" else "Analysis",
            figure_type="分组对比图" if language == "zh" else "breakdown chart",
            goal=(
                "把不同场景、参数组或实验子任务拆开对比，避免把所有结果压成一张总表"
                if language == "zh"
                else "Break down scenarios, parameter groups, or subtasks instead of compressing every result into one total table"
            ),
            evidence=_evidence_snippet(context, ("result_clues", "candidate_result_files", "candidate_config_files"), limit=3),
            keywords=("detail", "breakdown", "ablation", "sensitivity", "细节", "参数", "消融"),
            priority="medium",
        )

    if config_files and len(plans) < dynamic_max_items:
        add_plan(
            figure_id="deployment-or-config",
            section="实现细节" if language == "zh" else "Implementation",
            figure_type="部署/配置示意图" if language == "zh" else "deployment or configuration diagram",
            goal=(
                "说明关键配置项、运行环境和模块装配关系"
                if language == "zh"
                else "Explain the critical configuration, runtime environment, and module assembly relationships"
            ),
            evidence=_evidence_snippet(context, ("candidate_config_files", "stack"), limit=3),
            keywords=("config", "deploy", "runtime", "yaml", "json", "配置", "部署"),
            priority="medium",
        )

    return plans[:dynamic_max_items]


def build_figure_plan_summary(plan: list[dict[str, Any]], language: str, limit: int = 3) -> str:
    if not plan:
        if language == "zh":
            return "当前未生成独立图表计划，可在补充项目结果或结构信息后再次规划。"
        return "No dedicated figure plan is available yet; add project results or structure evidence and regenerate."

    captions = [str(item.get("caption") or "").strip() for item in plan[:limit] if str(item.get("caption") or "").strip()]
    if language == "zh":
        return "优先准备的图表包括：" + "；".join(captions)
    return "Priority figures: " + "; ".join(captions)


def build_table_plan(
    *,
    topic: str,
    language: str,
    project_context: dict[str, Any] | None,
    max_items: int = 4,
) -> list[dict[str, Any]]:
    context = project_context or {}
    table_candidates = [item for item in (context.get("table_candidates") or []) if isinstance(item, dict)]
    variable_inventory = [item for item in (context.get("variable_inventory") or []) if isinstance(item, dict)]
    dynamic_max_items = _plan_budget_total(context, "tables", max_items)
    plans: list[dict[str, Any]] = []

    for index, candidate in enumerate(table_candidates, start=1):
        if len(plans) >= dynamic_max_items:
            break
        caption_core = str(candidate.get("caption") or "").strip() or f"Table candidate {index}"
        section_role = str(candidate.get("section") or "experiment")
        plans.append(
            {
                "id": f"table-{index}",
                "section": _section_title_for_role(section_role, language),
                "caption": (f"表{len(plans) + 1} {caption_core}" if language == "zh" else f"Table {len(plans) + 1}. {caption_core}"),
                "goal": (
                    f"汇总 {caption_core} 中的关键指标、对比项和实验条件。"
                    if language == "zh"
                    else f"Summarize the key metrics, comparisons, and setup details from {caption_core}."
                ),
                "headers": list(candidate.get("headers") or []),
                "path": str(candidate.get("path") or ""),
                "metrics": list(candidate.get("metrics") or []),
                "source": str(candidate.get("source") or "project-table-scan"),
                "priority": "high" if section_role == "experiment" else "medium",
                "rows": [list(row) for row in candidate.get("preview_rows") or []],
            }
        )

    if variable_inventory and len(plans) < dynamic_max_items:
        plans.append(
            {
                "id": "notation-table",
                "section": _section_title_for_role("theory", language),
                "caption": ("表%d 主要符号说明" % (len(plans) + 1) if language == "zh" else f"Table {len(plans) + 1}. Main notation"),
                "goal": (
                    "汇总正文会用到的关键数学符号、含义和来源。"
                    if language == "zh"
                    else "Summarize the main mathematical symbols, meanings, and evidence source used in the paper."
                ),
                "headers": (["符号", "含义", "来源"] if language == "zh" else ["Symbol", "Meaning", "Evidence"]),
                "rows": [
                    [str(item.get("symbol") or ""), str(item.get("meaning") or ""), str(item.get("evidence") or "")]
                    for item in variable_inventory[:8]
                ],
                "path": "",
                "metrics": [],
                "source": "variable-inventory",
                "priority": "medium",
            }
        )

    return plans[:dynamic_max_items]


def build_equation_plan(
    *,
    topic: str,
    language: str,
    project_context: dict[str, Any] | None,
    max_items: int = 4,
) -> list[dict[str, Any]]:
    context = project_context or {}
    equation_candidates = [item for item in (context.get("equation_candidates") or []) if isinstance(item, dict)]
    dynamic_max_items = _plan_budget_total(context, "equations", max_items)
    plans: list[dict[str, Any]] = []
    for index, candidate in enumerate(equation_candidates[:dynamic_max_items], start=1):
        section_role = str(candidate.get("section") or "theory")
        focus = str(candidate.get("focus") or "").strip()
        plans.append(
            {
                "id": f"equation-{index}",
                "section": _section_title_for_role(section_role, language),
                "focus": focus,
                "source": str(candidate.get("source") or ""),
                "goal": (
                    f"将 {focus} 写成独立展示公式，并在公式后给出章节编号。"
                    if language == "zh"
                    else f"Render {focus} as a standalone display equation with chapter-aware numbering."
                ),
            }
        )
    return plans


def render_figure_plan_markdown(
    *,
    topic: str,
    language: str,
    plan: list[dict[str, Any]],
    table_plan: list[dict[str, Any]] | None = None,
    equation_plan: list[dict[str, Any]] | None = None,
) -> str:
    title = "图表规划" if language == "zh" else "Figure Plan"
    intro = (
        "这份规划在正文写作前锁定图、表、公式和符号资产，减少后期补图补表造成的返工。"
        if language == "zh"
        else "This plan locks figures, tables, equations, and notation assets before full drafting to reduce rewrite loops."
    )

    lines = [f"# {title}: {topic or 'Project Paper'}", "", intro, ""]

    if not plan and not table_plan and not equation_plan:
        lines.extend(
            [
                "当前还没有生成图表与公式候选资产，请先补充项目结构、结果文件或源码线索。"
                if language == "zh"
                else "No visual or equation assets are available yet. Add project structure, result files, or code evidence first.",
                "",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    if plan:
        lines.append("## 候选图片" if language == "zh" else "## Candidate Figures")
        lines.append("")
        lines.append(
            "| 图号 | 建议章节 | 类型 | 优先级 | 现有素材 |"
            if language == "zh"
            else "| Figure | Suggested Section | Type | Priority | Existing Asset |"
        )
        lines.append("| --- | --- | --- | --- | --- |")
        for item in plan:
            lines.append(
                f"| {item['caption']} | {item['section']} | {item['figure_type']} | {item['priority']} | {item['existing_asset'] or ('无' if language == 'zh' else 'No')} |"
            )
        lines.append("")

    if table_plan:
        lines.append("## 候选表格" if language == "zh" else "## Candidate Tables")
        lines.append("")
        lines.append(
            "| 表号 | 建议章节 | 目标 | 数据来源 |"
            if language == "zh"
            else "| Table | Suggested Section | Goal | Evidence Source |"
        )
        lines.append("| --- | --- | --- | --- |")
        for item in table_plan:
            lines.append(
                f"| {item['caption']} | {item['section']} | {item['goal']} | {item.get('path') or item.get('source') or ''} |"
            )
        lines.append("")

    if equation_plan:
        lines.append("## 候选公式" if language == "zh" else "## Candidate Equations")
        lines.append("")
        for item in equation_plan:
            lines.append(f"- {item['section']}: {item['goal']}")
        lines.append("")

    if plan:
        lines.append("## 逐项规格" if language == "zh" else "## Detailed Figure Specs")
        lines.append("")
        for item in plan:
            lines.append(f"### {item['caption']}")
            lines.append("")
            lines.append(f"- {'建议章节' if language == 'zh' else 'Suggested section'}: {item['section']}")
            lines.append(f"- {'图类型' if language == 'zh' else 'Figure type'}: {item['figure_type']}")
            lines.append(f"- {'目标' if language == 'zh' else 'Goal'}: {item['goal']}")
            lines.append(f"- {'证据' if language == 'zh' else 'Evidence'}: {item['evidence'] or ('使用项目上下文中的真实模块和结果字段' if language == 'zh' else 'Use real modules and result fields from project context')}")
            lines.append(f"- {'可复用素材' if language == 'zh' else 'Reusable asset'}: {item['existing_asset'] or ('暂无，建议新生成' if language == 'zh' else 'None, generate a new figure')}")
            lines.append(f"- {'视觉规格' if language == 'zh' else 'Visual spec'}: {item['visual_spec']}")
            lines.append(f"- {'生成提示' if language == 'zh' else 'Generator prompt'}:")
            lines.append("")
            lines.append("```text")
            lines.append(str(item["generator_prompt"]))
            lines.append("```")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_integrated_writing_assets_markdown() -> str:
    lines = [
        "# 写作资产清单",
        "",
        "当前写作链路已经吸收三类外部经验：阶段化论文工作流、图表优先规划、中文论文去模板化约束。",
        "",
        "## 集成来源",
        "",
    ]
    for source in WRITING_PROFILE_SOURCES:
        lines.append(f"- {source['name']}")
        lines.append(f"  - 来源：{source['url']}")
        lines.append(f"  - 吸收点：{source['focus']}")
    lines.extend(
        [
            "",
            "## 当前默认约束",
            "",
            "- 写作从项目分析、证据整理、图表规划、逐章生成、后处理几个阶段推进，不再依赖单次长 prompt 硬写到底。",
            "- 中文正文默认压低模板句、空泛总结和宣传式措辞，优先写模块关系、运行条件、参数约束和证据解释。",
            "- 图表在正文前先形成规划清单，优先判断放在哪一章、是否已有素材、需要什么视觉规格。",
            "- 实验章节默认优先引用真实结果文件和图像；如果缺乏结果，保留验证设计，不生成精确虚构数据。",
            "",
            "## 后续可直接复用的资产",
            "",
            "- `drafts/writing-assets.md`：当前写作策略和约束来源。",
            "- `drafts/figure-plan.md`：图表候选、章节位置和生成提示。",
            "- `drafts/project-analysis.md`：项目扫描和证据底稿。",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
