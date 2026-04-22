"""Shared domain detection and configuration for the paper generation pipeline.

Covers all major academic disciplines organized into paper archetypes:
- engineering: CS, robotics, control, electronics, mechanical, communication, power
- science: physics, chemistry, biology, materials, medicine
- data_analytics: economics, social science, education, psychology
- humanities: history, philosophy, literature, linguistics, law
- arts_design: architecture, design, music, art
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Domain keyword map (single source of truth for detection)
# ---------------------------------------------------------------------------
DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    # --- Engineering / CS ---
    "slam": (
        "slam", "建图", "地图构建", "localization", "mapping", "particle filter",
        "gmapping", "cartographer", "slam_toolbox", "hector_slam", "fast_slam",
    ),
    "navigation": (
        "nav2", "navigation", "导航", "path planning", "路径规划", "路径跟踪",
        "a*", "dwa", "mppi", "teb", "dubins", "coverage", "全覆盖", "planner",
    ),
    "control": (
        "pid", "控制", "control", "mpc", "model predictive", "adaptive control",
        "lqr", "robust control", "servo", "motion control", "运动控制",
    ),
    "ml_dl": (
        "neural network", "神经网络", "deep learning", "深度学习", "transformer", "cnn",
        "rnn", "lstm", "attention", "pytorch", "tensorflow", "训练", "training",
        "classification", "检测", "detection", "segmentation", "recognition", "识别",
    ),
    "signal_processing": (
        "signal processing", "信号处理", "fft", "filter", "滤波",
        "kalman", "卡尔曼", "频谱", "spectrum", "wavelet", "小波",
    ),
    "vision": (
        "computer vision", "计算机视觉", "image processing", "图像处理", "opencv",
        "camera", "相机", "visual", "stereo", "深度图", "point cloud", "点云",
        "3d reconstruction", "三维重建", "分割", "segmentation",
        "unet", "yolo", "resnet", "目标检测", "detection",
    ),
    "mechanical": (
        "有限元", "finite element", "ansys", "abaqus", "structural", "结构",
        "stress", "应变", "strain", "振动", "vibration",
        "热力学", "thermodynamic", "流体", "fluid",
    ),
    "communication": (
        "通信", "communication", "5g", "4g", "wireless", "信道", "channel",
        "调制", "modulation", "mimo", "天线", "antenna", "rf",
    ),
    "power_energy": (
        "电力", "power", "energy", "能源", "光伏", "solar", "电池", "battery",
        "储能", "microgrid", "微电网", "变压器",
    ),
    "software": (
        "software", "软件工程", "web", "app", "前端", "后端", "数据库",
        "微服务", "microservice", "api", "devops", "docker", "kubernetes",
        "软件架构", "敏捷", "agile", "rest", "graphql",
    ),
    # --- Natural Sciences (before data_analytics to get priority) ---
    "physics": (
        "物理", "physics", "量子", "quantum", "相对论", "relativity",
        "凝聚态", "condensed matter", "光学", "optics", "声学", "acoustics",
        "粒子物理", "particle physics", "核物理", "nuclear",
    ),
    "chemistry": (
        "化学", "chemistry", "分子", "molecule", "反应", "reaction",
        "催化", "catalysis", "合成", "synthesis", "有机", "organic",
        "高分子", "polymer", "纳米", "nano", "材料化学",
    ),
    "biology": (
        "生物", "biology", "基因", "gene", "蛋白质", "protein",
        "细胞", "cell", "dna", "rna", "基因组", "genome",
        "生态", "ecology", "进化", "evolution", "微生物", "microbe",
    ),
    "materials": (
        "材料科学", "materials science", "晶体", "crystal", "半导体",
        "semiconductor", "超导", "superconductor", "薄膜", "thin film",
        "复合材料", "composite", "陶瓷", "ceramic",
    ),
    "medicine": (
        "医学", "medicine", "临床", "clinical", "诊断", "diagnosis",
        "药物", "drug", "药理", "pharmacology", "病理", "pathology",
        "影像", "imaging", "手术", "surgery", "肿瘤", "tumor",
    ),
    # --- Data / Analytics / Social Science ---
    "economics": (
        "经济", "economics", "gdp", "通胀", "inflation", "财政",
        "fiscal", "货币", "monetary", "市场", "market", "贸易", "trade",
        "博弈", "game theory", "计量经济", "econometrics",
    ),
    "education": (
        "教育", "education", "教学", "teaching", "课程", "curriculum",
        "学习", "learning", "学生", "student", "教师", "teacher",
        "评估", "assessment", "教学法", "pedagogy",
    ),
    "psychology": (
        "心理", "psychology", "认知", "cognition", "行为", "behavior",
        "情绪", "emotion", "人格", "personality", "记忆", "memory",
        "实验心理", "experimental psychology", "焦虑", "anxiety",
        "抑郁", "压力", "stress", "动机", "motivation",
        "量表", "scale", "效应量", "方差分析",
    ),
    "social_science": (
        "社会学", "sociology", "调查", "survey", "文化", "culture",
        "城市化", "urbanization", "公共政策", "public policy", "治理", "governance",
    ),
    # --- Humanities ---
    "history": (
        "历史", "history", "朝代", "dynasty", "考古", "archaeology",
        "史料", "historical source", "文明", "civilization", "近代", "modern",
        "革命", "revolution", "殖民", "colonial", "变迁", "演变",
        "宋代", "唐代", "明代", "清代", "汉代", "先秦", "明清",
        "宋朝", "唐朝", "明朝", "清朝", "汉朝",
    ),
    "philosophy": (
        "哲学", "philosophy", "伦理", "ethics", "认识论", "epistemology",
        "形而上学", "metaphysics", "逻辑", "logic", "美学", "aesthetics",
        "存在主义", "existentialism", "现象学", "phenomenology",
    ),
    "literature": (
        "文学", "literature", "小说", "novel", "诗歌", "poetry",
        "戏剧", "drama", "散文", "prose", "作家", "author",
        "叙事", "narrative", "文本分析", "textual analysis",
    ),
    "linguistics": (
        "语言学", "linguistics", "语法", "grammar", "语义", "semantics",
        "语用", "pragmatics", "语音", "phonetics", "语料库", "corpus",
        "自然语言处理", "nlp", "翻译", "translation",
    ),
    "law": (
        "法律", "law", "法规", "regulation", "司法", "judicial",
        "宪法", "constitution", "刑法", "criminal", "民法", "civil",
        "合同", "contract", "知识产权", "intellectual property",
    ),
    # --- Arts / Design ---
    "architecture_design": (
        "建筑", "architecture", "设计", "design", "规划", "planning",
        "空间", "space", "景观", "landscape", "室内", "interior",
        "城市设计", "urban design",
    ),
    "music": (
        "音乐", "music", "作曲", "composition", "和声", "harmony",
        "乐器", "instrument", "音律", "temperament", "乐谱", "score",
    ),
    "art": (
        "美术", "fine art", "绘画", "painting", "雕塑", "sculpture",
        "视觉艺术", "visual art", "当代艺术", "contemporary art",
        "数字艺术", "digital art",
    ),
}

# ---------------------------------------------------------------------------
# Paper archetypes: each domain maps to an archetype that determines
# the chapter structure, tone, and formula requirements.
# ---------------------------------------------------------------------------
ARCHETYPE_ENGINEERING = "engineering"
ARCHETYPE_SCIENCE = "science"
ARCHETYPE_DATA_ANALYTICS = "data_analytics"
ARCHETYPE_HUMANITIES = "humanities"
ARCHETYPE_ARTS = "arts"

DOMAIN_TO_ARCHETYPE: dict[str, str] = {
    # Engineering / CS
    "slam": ARCHETYPE_ENGINEERING,
    "navigation": ARCHETYPE_ENGINEERING,
    "control": ARCHETYPE_ENGINEERING,
    "ml_dl": ARCHETYPE_ENGINEERING,
    "signal_processing": ARCHETYPE_ENGINEERING,
    "vision": ARCHETYPE_ENGINEERING,
    "mechanical": ARCHETYPE_ENGINEERING,
    "communication": ARCHETYPE_ENGINEERING,
    "power_energy": ARCHETYPE_ENGINEERING,
    "software": ARCHETYPE_ENGINEERING,
    # Natural Sciences
    "physics": ARCHETYPE_SCIENCE,
    "chemistry": ARCHETYPE_SCIENCE,
    "biology": ARCHETYPE_SCIENCE,
    "materials": ARCHETYPE_SCIENCE,
    "medicine": ARCHETYPE_SCIENCE,
    # Data / Analytics / Social Science
    "economics": ARCHETYPE_DATA_ANALYTICS,
    "social_science": ARCHETYPE_DATA_ANALYTICS,
    "education": ARCHETYPE_DATA_ANALYTICS,
    "psychology": ARCHETYPE_DATA_ANALYTICS,
    # Humanities
    "history": ARCHETYPE_HUMANITIES,
    "philosophy": ARCHETYPE_HUMANITIES,
    "literature": ARCHETYPE_HUMANITIES,
    "linguistics": ARCHETYPE_HUMANITIES,
    "law": ARCHETYPE_HUMANITIES,
    # Arts / Design
    "architecture_design": ARCHETYPE_ARTS,
    "music": ARCHETYPE_ARTS,
    "art": ARCHETYPE_ARTS,
}

# Per-archetype Chinese chapter structure (section titles + share + blueprint points)
ARCHETYPE_BLUEPRINTS: dict[str, list[dict[str, Any]]] = {
    ARCHETYPE_ENGINEERING: [
        {
            "title": "1. 绪论",
            "share": 0.12,
            "points": ["研究背景与问题提出", "国内外研究现状", "研究内容与论文组织"],
        },
        {
            "title": "2. 相关技术原理与数学模型",
            "share": 0.25,
            "points": [
                "核心技术基础与平台介绍",
                "核心算法数学建模（从问题定义出发，逐步推导核心公式，使用 $$...$$ 包裹独立公式）",
                "公式推导完整链条：定义→假设→推导→结论，编号如 (2.1)，每步标注推导依据",
                "算法伪代码或流程图描述",
                "算法复杂度分析与正确性讨论",
            ],
        },
        {
            "title": "3. 系统设计与算法实现",
            "share": 0.23,
            "points": [
                "总体架构与模块划分",
                "关键数据结构与参数配置",
                "算法实现要点（核心逻辑、边界处理、工程取舍）",
                "系统工作流程",
            ],
        },
        {
            "title": "4. 实验设计与结果分析",
            "share": 0.28,
            "points": [
                "实验环境与参数设置（硬件/软件平台）",
                "实验场景描述（测试用例、对比基线）",
                "评价指标定义",
                "实验结果表格与图表（必须包含数值数据）",
                "结果分析与对比讨论",
                "消融实验或参数敏感性分析",
            ],
        },
        {
            "title": "5. 结论与展望",
            "share": 0.12,
            "points": ["研究结论", "创新点与贡献", "不足分析与后续改进方向"],
        },
    ],
    ARCHETYPE_SCIENCE: [
        {
            "title": "1. 引言",
            "share": 0.10,
            "points": ["研究背景与科学问题", "国内外研究进展", "研究目的与意义"],
        },
        {
            "title": "2. 理论基础与文献综述",
            "share": 0.20,
            "points": [
                "核心概念与理论框架",
                "关键公式的数学推导（使用 $$...$$ 包裹独立公式，编号如 (2.1)）",
                "研究现状总结与现有方法的局限性",
            ],
        },
        {
            "title": "3. 研究方法",
            "share": 0.22,
            "points": [
                "实验设计与方法论",
                "材料/样本/数据来源描述",
                "关键实验参数与仪器设备",
                "数据处理与分析方法",
            ],
        },
        {
            "title": "4. 结果与讨论",
            "share": 0.35,
            "points": [
                "实验结果呈现（表格与图表，必须包含数值数据）",
                "结果分析与物理解释",
                "与已有研究的对比讨论",
                "异常现象与误差分析",
            ],
        },
        {
            "title": "5. 结论",
            "share": 0.13,
            "points": ["主要研究发现", "理论贡献与实践意义", "研究局限与未来方向"],
        },
    ],
    ARCHETYPE_DATA_ANALYTICS: [
        {
            "title": "1. 引言",
            "share": 0.10,
            "points": ["研究背景与现实问题", "文献综述与研究缺口", "研究目标与论文结构"],
        },
        {
            "title": "2. 文献综述与理论基础",
            "share": 0.18,
            "points": [
                "核心概念界定",
                "理论框架与假设提出",
                "相关研究方法综述",
            ],
        },
        {
            "title": "3. 研究设计与方法",
            "share": 0.22,
            "points": [
                "研究假设与变量定义",
                "数据来源与样本描述",
                "模型构建与估计方法（给出关键公式，使用 $$...$$ 包裹独立公式）",
                "变量度量与操作化定义",
            ],
        },
        {
            "title": "4. 实证分析",
            "share": 0.35,
            "points": [
                "描述性统计分析（表格呈现）",
                "回归/模型估计结果（系数表、显著性标注）",
                "稳健性检验与内生性处理",
                "结果讨论与经济/社会含义解读",
            ],
        },
        {
            "title": "5. 结论与政策建议",
            "share": 0.15,
            "points": ["主要结论", "理论贡献", "实践/政策建议", "研究局限与展望"],
        },
    ],
    ARCHETYPE_HUMANITIES: [
        {
            "title": "1. 绪论",
            "share": 0.12,
            "points": ["研究背景与问题意识", "研究意义与学术价值", "研究思路与论文结构"],
        },
        {
            "title": "2. 文献综述与理论框架",
            "share": 0.20,
            "points": [
                "核心概念界定与学术史梳理",
                "国内外研究现状与学术争论",
                "理论框架与分析视角的确立",
            ],
        },
        {
            "title": "3. 研究方法与资料来源",
            "share": 0.15,
            "points": [
                "研究方法选择与方法论依据",
                "资料/文献/数据的收集与整理",
                "分析框架的操作化",
            ],
        },
        {
            "title": "4. 主体论证",
            "share": 0.40,
            "points": [
                "核心论点的展开与论证",
                "多角度/多层次分析",
                "案例/文本/史料的深入解读",
                "与既有观点的对话与辩驳",
            ],
        },
        {
            "title": "5. 结论",
            "share": 0.13,
            "points": ["研究结论总结", "学术贡献", "研究不足与后续方向"],
        },
    ],
    ARCHETYPE_ARTS: [
        {
            "title": "1. 引言",
            "share": 0.10,
            "points": ["创作/设计背景", "研究问题与目标", "论文结构"],
        },
        {
            "title": "2. 相关理论与案例研究",
            "share": 0.20,
            "points": [
                "理论基础与设计原则",
                "国内外经典案例分析",
                "现有方法的优缺点评述",
            ],
        },
        {
            "title": "3. 设计/创作方法",
            "share": 0.25,
            "points": [
                "设计理念与创作思路",
                "方法流程与技术路线",
                "工具、材料与实现过程",
            ],
        },
        {
            "title": "4. 作品呈现与评价",
            "share": 0.30,
            "points": [
                "作品/方案展示与说明",
                "评价标准与方法",
                "用户/专家反馈与数据分析",
                "与同类作品的比较讨论",
            ],
        },
        {
            "title": "5. 结论",
            "share": 0.15,
            "points": ["创作/设计总结", "创新点", "不足与改进方向"],
        },
    ],
}

# Per-domain evidence terms
DOMAIN_EVIDENCE: dict[str, tuple[str, ...]] = {
    "slam": ("SLAM", "地图", "建图", "定位", "里程计", "雷达", "点云", "位姿估计", "闭环检测", "栅格地图"),
    "navigation": ("导航", "路径规划", "轨迹", "避障", "代价地图", "全局规划", "局部控制", "目标点"),
    "control": ("控制", "PID", "MPC", "状态空间", "反馈", "阶跃响应", "超调", "稳态误差"),
    "ml_dl": ("训练", "损失函数", "准确率", "模型", "推理", "特征", "梯度", "优化器", "验证集"),
    "signal_processing": ("频谱", "滤波", "FFT", "采样", "噪声", "信噪比", "时域", "频域"),
    "vision": ("图像", "特征提取", "卷积", "检测", "分割", "深度图", "相机标定"),
    "mechanical": ("应力", "应变", "有限元", "网格", "模态", "振动", "刚度", "强度", "疲劳"),
    "communication": ("信噪比", "误码率", "带宽", "调制", "信道", "天线", "吞吐量"),
    "power_energy": ("电压", "电流", "功率", "效率", "潮流", "光伏", "储能", "微电网", "负载"),
    "software": ("模块", "接口", "服务", "部署", "测试", "重构", "性能", "可扩展性"),
    "physics": ("实验", "测量", "理论", "模型", "验证", "误差", "统计", "分布"),
    "chemistry": ("实验", "反应", "产率", "纯度", "表征", "光谱", "色谱", "催化活性"),
    "biology": ("实验", "样本", "对照组", "显著性", "表达", "活性", "抑制", "增殖"),
    "materials": ("性能", "表征", "晶体结构", "力学性能", "热性能", "微观形貌"),
    "medicine": ("临床", "诊断", "疗效", "对照组", "显著性", "生存率", "不良反应"),
    "economics": ("回归", "显著性", "系数", "样本", "面板数据", "内生性", "稳健性"),
    "social_science": ("调查", "样本", "回归", "显著性", "相关性", "访谈", "质性分析"),
    "education": ("实验组", "对照组", "前测", "后测", "显著性", "效果量", "量表"),
    "psychology": ("被试", "实验", "显著性", "效应量", "相关性", "方差分析", "量表"),
    "history": ("史料", "文献", "档案", "考证", "比较", "背景", "演变", "影响"),
    "philosophy": ("论证", "概念", "理论", "批判", "逻辑", "命题", "推理", "范式"),
    "literature": ("文本", "叙事", "意象", "隐喻", "主题", "结构", "风格", "象征"),
    "linguistics": ("语料", "语法", "语义", "语用", "句法", "形态", "音系", "类型学"),
    "law": ("法条", "判例", "司法解释", "法律关系", "权利", "义务", "构成要件", "法律适用"),
    "architecture_design": ("空间", "功能", "形式", "结构", "材料", "环境", "使用者", "规范"),
    "music": ("旋律", "和声", "节奏", "曲式", "配器", "音色", "调性", "复调"),
    "art": ("形式", "色彩", "构图", "材料", "观念", "表现", "风格", "媒介"),
    "general": ("系统", "模块", "接口", "参数", "配置", "性能", "实验", "结果", "数据", "功能"),
}

# Per-domain default keywords for submission
DOMAIN_DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "slam": ["SLAM", "建图", "定位", "自主导航"],
    "navigation": ["路径规划", "导航", "运动控制", "避障"],
    "control": ["控制系统", "PID", "MPC", "鲁棒性"],
    "ml_dl": ["深度学习", "神经网络", "训练", "推理"],
    "signal_processing": ["信号处理", "滤波", "频谱分析"],
    "vision": ["计算机视觉", "图像处理", "目标检测"],
    "mechanical": ["有限元", "结构分析", "力学建模"],
    "communication": ["通信系统", "信道", "调制解调"],
    "power_energy": ["电力系统", "能源管理", "储能"],
    "software": ["软件工程", "系统设计", "架构", "测试"],
    "physics": ["物理", "实验验证", "理论模型", "数值模拟"],
    "chemistry": ["化学", "合成", "催化", "表征"],
    "biology": ["生物学", "基因", "细胞", "实验"],
    "materials": ["材料科学", "性能表征", "微观结构"],
    "medicine": ["医学", "临床", "诊断", "疗效"],
    "economics": ["经济学", "实证分析", "回归分析", "政策"],
    "social_science": ["社会学", "调查研究", "数据分析"],
    "education": ["教育学", "教学设计", "效果评估"],
    "psychology": ["心理学", "实验研究", "统计分析"],
    "history": ["历史研究", "史料分析", "比较研究"],
    "philosophy": ["哲学", "理论分析", "逻辑论证"],
    "literature": ["文学研究", "文本分析", "比较文学"],
    "linguistics": ["语言学", "语料分析", "语法研究"],
    "law": ["法学", "法律分析", "案例研究"],
    "architecture_design": ["建筑设计", "空间设计", "规划设计"],
    "music": ["音乐", "作曲分析", "音乐理论"],
    "art": ["艺术", "创作研究", "视觉分析"],
    "general": ["系统设计", "工程实现", "实验验证"],
}


def detect_domain(topic: str, project_context: dict[str, Any] | None) -> str:
    """Unified domain detection for the entire pipeline.

    Scans title, project_summary, source/config files, and method_clues
    against keyword maps. Returns one of the DOMAIN_KEYWORDS keys or 'general'.
    """
    haystack = " ".join(
        filter(
            None,
            [
                topic.lower(),
                str((project_context or {}).get("project_summary", "")).lower(),
                " ".join(
                    str(f) for f in (project_context or {}).get("candidate_source_files", []) or []
                ),
                " ".join(
                    str(f) for f in (project_context or {}).get("candidate_config_files", []) or []
                ),
                " ".join(
                    str(f) for f in (project_context or {}).get("method_clues", []) or []
                ),
            ],
        )
    )
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            return domain
    return "general"


def get_archetype(topic: str, project_context: dict[str, Any] | None) -> str:
    """Return the paper archetype for the detected domain."""
    domain = detect_domain(topic, project_context)
    return DOMAIN_TO_ARCHETYPE.get(domain, ARCHETYPE_ENGINEERING)


def get_blueprint(topic: str, project_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the chapter blueprint for the detected domain's archetype."""
    archetype = get_archetype(topic, project_context)
    return ARCHETYPE_BLUEPRINTS.get(archetype, ARCHETYPE_BLUEPRINTS[ARCHETYPE_ENGINEERING])


def get_evidence_terms(topic: str, project_context: dict[str, Any] | None) -> tuple[str, ...]:
    """Return evidence hint keywords for the detected domain."""
    domain = detect_domain(topic, project_context)
    return DOMAIN_EVIDENCE.get(domain, DOMAIN_EVIDENCE["general"])


def get_default_keywords(domain: str) -> list[str]:
    """Return default submission keywords for a domain."""
    return list(DOMAIN_DEFAULT_KEYWORDS.get(domain, DOMAIN_DEFAULT_KEYWORDS["general"]))


# ---------------------------------------------------------------------------
# Per-archetype figure & table budget (dynamic, based on paper length)
# ---------------------------------------------------------------------------
# Each archetype defines how many figures and tables to include, broken down
# by chapter. These are *minimum* targets — the LLM may add more if content
# warrants it. The budget scales with target word count.
#
# figure_budget: {chapter_index: {type: count, ...}}
#   type: "architecture" (system/block diagrams), "result" (bar/line/scatter),
#         "comparison" (side-by-side), "process" (flow/pipeline), "qualitative" (screenshot/example)
# table_budget: {chapter_index: count}
# ---------------------------------------------------------------------------

ARCHETYPE_FIGURE_BUDGET: dict[str, dict[int, list[str]]] = {
    ARCHETYPE_ENGINEERING: {
        2: ["principle", "design"],               # 技术原理: 算法原理图 + 架构/流程图
        3: ["design", "process"],                  # 系统设计: 模块框图 + 工作流程图
        4: ["scene", "result", "result", "comparison", "result", "comparison"],
        # 实验: 场景图 + 主结果 + 过程图 + 对比图 + 消融/参数图 + 雷达图
    },
    ARCHETYPE_SCIENCE: {
        2: ["principle", "design"],                # 理论基础: 原理图 + 理论框架图
        3: ["design", "process"],                  # 研究方法: 实验装置 + 操作流程图
        4: ["scene", "result", "result", "comparison", "result", "result"],
        # 结果: 场景 + 主结果 + 趋势图 + 对比图 + 误差分析 + 补充结果
    },
    ARCHETYPE_DATA_ANALYTICS: {
        2: ["principle"],                          # 文献综述: 理论框架图
        3: ["design", "process"],                  # 研究设计: 模型框架 + 变量关系图
        4: ["scene", "result", "result", "comparison", "result", "result"],
        # 实证: 数据概览 + 回归系数 + 稳健性检验 + 对比图 + 异质性分析 + 补充结果
    },
    ARCHETYPE_HUMANITIES: {
        3: ["process"],                            # 研究方法: 分析框架图
        4: ["result", "result", "result", "comparison", "result"],
        # 主体论证: 核心论据图 + 史料/文本对比 + 演变趋势 + 多维对比 + 补充论据
    },
    ARCHETYPE_ARTS: {
        2: ["result", "comparison"],               # 相关理论: 经典案例图 + 对比分析图
        3: ["process", "process"],                 # 设计方法: 创作流程 + 技术路线图
        4: ["result", "result", "result", "comparison", "result"],
        # 作品呈现: 主作品 + 细节特写 + 用户反馈 + 同类对比 + 补充展示
    },
}

ARCHETYPE_TABLE_BUDGET: dict[str, dict[int, int]] = {
    ARCHETYPE_ENGINEERING: {
        2: 1,  # 符号/参数表
        3: 1,  # 配置参数表
        4: 3,  # 主结果 + 消融 + 参数敏感性
    },
    ARCHETYPE_SCIENCE: {
        2: 1,  # 实验材料/参数表
        3: 2,  # 实验条件 + 样本描述表
        4: 3,  # 主结果 + 对比 + 补充数据
    },
    ARCHETYPE_DATA_ANALYTICS: {
        3: 2,  # 变量定义 + 描述统计表
        4: 4,  # 回归结果 + 稳健性 + 异质性 + 补充回归
    },
    ARCHETYPE_HUMANITIES: {
        3: 1,  # 资料/样本来源表
        4: 2,  # 核心论据汇总 + 补充材料
    },
    ARCHETYPE_ARTS: {
        3: 1,  # 材料/工具表
        4: 2,  # 评价结果 + 用户反馈表
    },
}


def get_figure_budget(archetype: str, chapter_index: int) -> list[str]:
    """Return list of figure types recommended for a chapter.

    chapter_index is 1-based (1=绪论, 2=技术原理, etc.)
    """
    budget = ARCHETYPE_FIGURE_BUDGET.get(archetype, ARCHETYPE_FIGURE_BUDGET[ARCHETYPE_ENGINEERING])
    return list(budget.get(chapter_index, []))


def get_table_budget(archetype: str, chapter_index: int) -> int:
    """Return minimum table count recommended for a chapter."""
    budget = ARCHETYPE_TABLE_BUDGET.get(archetype, ARCHETYPE_TABLE_BUDGET[ARCHETYPE_ENGINEERING])
    return budget.get(chapter_index, 0)


def get_total_figure_budget(archetype: str) -> int:
    """Return total minimum figure count for the paper."""
    budget = ARCHETYPE_FIGURE_BUDGET.get(archetype, ARCHETYPE_FIGURE_BUDGET[ARCHETYPE_ENGINEERING])
    return sum(len(figs) for figs in budget.values())


def get_total_table_budget(archetype: str) -> int:
    """Return total minimum table count for the paper."""
    budget = ARCHETYPE_TABLE_BUDGET.get(archetype, ARCHETYPE_TABLE_BUDGET[ARCHETYPE_ENGINEERING])
    return sum(budget.values())


def get_figure_table_instruction(archetype: str, topic: str = "", target_words: int = 15000) -> str:
    """Generate a Chinese instruction string for the LLM specifying figure/table requirements.

    This is injected into the paper writing prompt to guide the LLM on how many
    figures and tables to include, broken down by chapter.

    The budget is dynamically scaled by *target_words*: the baseline is calibrated
    for 15 000 characters.  For shorter papers the figure/table counts are reduced
    proportionally (clamped to a minimum of 1 figure and 0 tables per chapter).
    """
    _scale = max(0.3, min(1.0, target_words / 15000))

    fig_budget = ARCHETYPE_FIGURE_BUDGET.get(archetype, ARCHETYPE_FIGURE_BUDGET[ARCHETYPE_ENGINEERING])
    tbl_budget = ARCHETYPE_TABLE_BUDGET.get(archetype, ARCHETYPE_TABLE_BUDGET[ARCHETYPE_ENGINEERING])

    # Scale figure counts per chapter
    scaled_fig_budget: dict[int, list[str]] = {}
    for ch, types in fig_budget.items():
        scaled_count = max(1, round(len(types) * _scale))
        scaled_fig_budget[ch] = types[:scaled_count]

    # Scale table counts per chapter
    scaled_tbl_budget: dict[int, int] = {}
    for ch, count in tbl_budget.items():
        scaled_tbl_budget[ch] = max(0, round(count * _scale))

    total_figs = sum(len(v) for v in scaled_fig_budget.values())
    total_tbls = sum(scaled_tbl_budget.values())

    lines = [f"图表预算（最低要求）：全文不少于 {total_figs} 张图、{total_tbls} 个表。各章分配如下：\n"]

    # Figure type descriptions (aligned with image_roles.py roles)
    fig_type_desc = {
        "principle": "算法原理/理论示意图",
        "design": "系统架构/模块框图",
        "scene": "实验场景/环境设置图",
        "process": "流程图/工作流程图",
        "result": "实验结果图（柱状图、曲线图、散点图等）",
        "comparison": "对比分析图（雷达图、热力图等）",
        "qualitative": "定性展示图（截图、样例、实物照片等）",
    }

    chapter_names = {1: "绪论/引言", 2: "技术原理/理论基础", 3: "系统设计/研究方法",
                     4: "实验与结果/实证分析", 5: "结论与展望"}

    all_chapters = sorted(set(list(scaled_fig_budget.keys()) + list(scaled_tbl_budget.keys())))
    for ch in all_chapters:
        ch_name = chapter_names.get(ch, f"第{ch}章")
        fig_types = scaled_fig_budget.get(ch, [])
        tbl_count = scaled_tbl_budget.get(ch, 0)
        parts = []
        if fig_types:
            type_strs = [fig_type_desc.get(t, t) for t in fig_types]
            parts.append(f"{len(fig_types)} 张图（{'、'.join(type_strs)}）")
        if tbl_count > 0:
            parts.append(f"{tbl_count} 个表")
        if parts:
            lines.append(f"- {ch_name}：{'，'.join(parts)}")

    lines.append(f"\n注意：以上为最低要求，如果论文内容丰富，可适当增加图表数量。每张图/表必须有对应的编号和标题。")

    if topic:
        lines.append(f"论文主题：{topic}")

    return "\n".join(lines)
