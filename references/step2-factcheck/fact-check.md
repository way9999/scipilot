# Step 2: 信息检索 (Fact-Check)

> 为后续生成提供"可核查的事实锚点"

## 目标

基于 Step 1 采集的研究意图，通过学术文献检索构建事实知识库，确保开题报告中的：
- **背景陈述** 有文献支撑
- **方法选择** 有先例参考
- **问题定义** 有现实依据

---

## 前置条件

- Step 1 已完成（至少 MVP 模式）
- 已获取：学科方向(Q2)、研究对象(Q3)、方法视角(Q5)

---

## Step 1: 工具检测

**执行逻辑：**

```
function detectSearchTool():
    try:
        result = mcp__grok-search__get_config_info()
        if result.connection_test.status == "success":
            return "grok-search"
    catch:
        pass
    return "WebSearch"  // 回退到官方工具
```

**工具能力对比：**

| 工具 | 优势 | 限制 |
|------|------|------|
| grok-search | 实时性强、支持platform过滤 | 需配置API |
| WebSearch | 开箱即用 | 学术定向能力较弱 |

**执行时机：** Step 2 启动时自动检测一次，结果缓存至会话结束

---

## Step 2: 关键词提取

从 Step 1 答案中提取检索种子：

| 来源 | 提取内容 | 示例 |
|------|----------|------|
| Q2 学科方向 | 领域限定词 | "计算机视觉", "材料科学" |
| Q3 研究对象 | 核心实体 | "图神经网络", "锂电池正极材料" |
| Q5 方法视角 | 方法关键词 | "Transformer", "第一性原理计算" |
| Q7 现有不足 | 问题关键词 | "可解释性", "循环寿命" |

**提取策略：**
```
function extractKeywords(step1_answers):
    keywords = {
        domain: answers.Q2.discipline,
        entity: answers.Q3.core_terms,
        method: answers.Q5.selected_methods,
        problem: answers.Q7.pain_points (if exists)
    }
    return deduplicate(flatten(keywords))
```

---

## Step 3: 检索策略

### 3.1 数据库优先级

根据学科方向自动路由（参考 [venue-registry.md](venue-registry.md)）：

| 学科 | 首选数据库 | 备选 |
|------|------------|------|
| 计算机/AI | arXiv, ACM, IEEE | Google Scholar |
| 生物医学 | PubMed, bioRxiv | Google Scholar |
| 材料/化学 | Web of Science, Nature系列 | Google Scholar |
| 经管 | SSRN, JSTOR | Google Scholar |
| 通用/跨学科 | Google Scholar | arXiv |

### 3.2 Query 构建模板

**背景文献检索：**
```
"{研究对象} survey OR review {year:近5年}"
"{研究对象} {领域} state-of-the-art"
```

**方法参考检索：**
```
"{方法关键词} {研究对象} {顶会/顶刊名}"
"{方法关键词} application {领域}"
```

**问题佐证检索：**
```
"{问题关键词} challenge OR limitation {领域}"
"{研究对象} {问题关键词} analysis"
```

### 3.3 执行示例

```
// 示例：计算机视觉 + 图神经网络 + 可解释性
queries = [
    "graph neural network computer vision survey 2023 2024",
    "GNN explainability site:arxiv.org",
    "graph neural network interpretability NeurIPS OR ICML OR ICLR"
]
```

---

## Step 4: 循环工作流

### 4.1 覆盖度阈值

| 维度 | 最低要求 | 建议目标 |
|------|----------|----------|
| 背景文献 | ≥3篇 | 5篇 |
| 方法参考 | ≥2篇 | 3篇 |
| 问题佐证 | ≥2篇 | 3篇 |

**总覆盖率 = 已获取 / 目标总数 × 100%**

### 4.2 迭代流程

```
iteration = 0
MAX_ITERATIONS = 5

while iteration < MAX_ITERATIONS:
    iteration++

    // 1. 执行检索
    results = search(currentQueries, selectedTool)

    // 2. 筛选有效文献（标题+摘要相关性判断）
    validPapers = filter(results, relevanceScore > 0.7)

    // 3. 记录到知识库
    for paper in validPapers:
        saveSummary(paper, "knowledge-base/papers/")
        appendBibTeX(paper, "knowledge-base/sources.bib")

    // 4. 评估覆盖度
    coverage = assessCoverage()
    updateREADME(coverage)

    // 5. 判断终止条件
    if coverage.allDimensionsMet:
        break
    else:
        // 6. 关键词精化
        gaps = identifyGaps(coverage)
        currentQueries = refineQueries(gaps)

        // 7. 用户确认是否继续
        if not userConfirmContinue():
            break

// 最终确认
presentSummary(knowledgeBase)
userFinalApproval()
```

### 4.3 关键词精化策略

当某维度文献不足时：

| 情况 | 精化方向 |
|------|----------|
| 背景文献不足 | 扩大时间范围、添加同义词 |
| 方法参考不足 | 搜索方法变体、相关技术 |
| 问题佐证不足 | 扩展问题表述、搜索领域综述 |

---

## Step 5: 文献记录规范

### 5.1 单篇文献摘要模板

文件命名：`knowledge-base/papers/{first-author-year-keyword}.md`

```markdown
# {论文标题}

## 元信息
- **作者**: {作者列表}
- **年份**: {年份}
- **发表于**: {期刊/会议}
- **DOI/URL**: {链接}
- **BibTeX Key**: @{citationKey}

## 分类标签
- [ ] 背景文献
- [ ] 方法参考
- [ ] 问题佐证

## 核心摘要
{2-3句话概括论文核心贡献}

## 与本研究关联
{说明该文献如何支撑开题报告的哪个部分}

## 关键引用句
> "{可直接引用的关键结论}"
```

### 5.2 BibTeX 格式

```bibtex
@article{AuthorYear_keyword,
    author    = {Author, First and Author, Second},
    title     = {Paper Title},
    journal   = {Journal Name},
    year      = {2024},
    volume    = {XX},
    pages     = {XXX--XXX},
    doi       = {10.xxxx/xxxxx}
}
```

---

## Step 6: 输出物

Step 2 完成后交付：

1. **knowledge-base/README.md** - 更新后的索引与覆盖度状态
2. **knowledge-base/sources.bib** - 完整的BibTeX引用库
3. **knowledge-base/papers/*.md** - 各文献的结构化摘要
4. **检索报告** - 向用户汇报：
   - 总检索轮次
   - 各维度覆盖情况
   - 关键文献清单（按重要性排序）
   - 建议补充方向（如有）

---

## 与后续Step衔接

Step 2 输出将作为 Step 3（结构冻结）的输入：
- 背景文献 → 选题背景章节
- 方法参考 → 技术路线章节
- 问题佐证 → 问题提出章节

---

## 异常处理

| 情况 | 处理策略 |
|------|----------|
| 搜索工具不可用 | 提示用户检查网络/配置 |
| 领域文献稀少 | 扩展到相邻领域、放宽年份限制 |
| 用户多次跳过确认 | 保存当前进度，允许后续恢复 |
| 达到最大迭代仍不满足 | 标记不足维度，建议用户手动补充 |
