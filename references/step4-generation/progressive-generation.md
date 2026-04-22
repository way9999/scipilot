# Step 4: 渐进式生成 (Progressive Generation)

> 围绕既定大纲，通过持续交互按模块逐步展开具体内容，并根据用户的反馈动态修正研究重点与表达方式。

## 目标

基于 `outline.md`（冻结大纲）逐章生成开题报告正文，确保：
- **内容完整**：覆盖大纲所有要点
- **用词专业**：符合学科术语规范
- **风格统一**：适配领域写作习惯
- **引用准确**：正确引用知识库文献

---

## 前置条件

- Step 3 已完成（大纲已冻结）
- `outline.md` 存在且 `status: frozen`
- `knowledge-base/` 文献充足

---

## 核心流程

```
1. 加载冻结大纲 → outline.md
2. 加载写作风格 → writing-style-adapters.md
3. 按章节顺序生成：
   for chapter in outline:
       a. 读取章节配置（篇幅、要点、关联文献）
       b. 生成初稿
       c. 用户审阅与反馈
       d. 迭代修订直至确认
       e. 标记章节完成
4. 汇总成完整报告
5. 全文一致性检查
```

---

## Step 4.1: 加载写作风格

根据 Q2（学科方向）加载对应写作规范：

```python
def load_writing_style(discipline):
    if discipline in STYLE_ADAPTERS:
        return STYLE_ADAPTERS[discipline]
    return GENERIC_STYLE
```

写作风格配置见 [writing-style-adapters.md](writing-style-adapters.md)

---

## Step 4.2: 章节生成策略

### 生成输入

每章生成时读取：

```yaml
chapter:
  title: "2. 国内外研究现状"
  required: true
  word_count: 2000-2500
  subsections:
    - "2.1 领域发展概述"
    - "2.2 代表性研究综述"
    - "2.3 现有研究局限"
    - "2.4 本研究切入点"
  references:
    - "@Wang2023_survey"
    - "@Zhang2023_method"
  hints:
    - "按时间线梳理发展脉络"
    - "重点分析近3年进展"
    - "指出现有方法的共性问题"
```

### 生成约束

| 约束项 | 说明 |
|--------|------|
| 篇幅控制 | 严格遵循字数范围，±10%容差 |
| 引用嵌入 | 关键论点必须有文献支撑 |
| 术语规范 | 使用学科标准术语 |
| 逻辑连贯 | 段落间有承接过渡 |
| 风格统一 | 符合领域写作习惯 |
| 句长控制 | 英文 15-20 词/句，中文 30-50 字/句 |
| 散文化 | 最终输出不含项目符号列表（技术路线章节除外） |
| 限定词密度 | "显著"、"重要"、"关键"等限定词 ≤3次/千字 |
| 冗余消除 | 删除"进行了研究"→"研究了"、"作出了分析"→"分析了"等冗余表达 |

### 生成模板（按章节类型）

**选题背景类：**
```
[宏观背景] → [领域聚焦] → [具体问题] → [研究意义]
```

**文献综述类：**
```
[发展脉络] → [代表性工作] → [方法对比] → [现有局限] → [本研究切入]
```

**技术路线类：**
```
[整体思路] → [关键步骤] → [技术细节] → [创新点说明]
```

## Step 4.2.5: 段落质量门控

每段生成后，执行三层质量检查：

### 词汇层

中英文禁用/替换表：

| 原表达 | 替换为 | 原因 |
|--------|--------|------|
| 进行了研究 | 研究了 | 冗余动词结构 |
| 作出了分析 | 分析了 | 冗余动词结构 |
| 对...进行了探讨 | 探讨了... | 冗余动词结构 |
| in order to | to | 冗余 |
| it is worth noting that | [删除，直接陈述] | AI 痕迹 |
| a large number of | many / numerous | 冗余 |

### 结构层

- **句式重复检测**：连续 3 句不得以相同词语/句式开头
- **段落长度检测**：单段超过 400 字（中文）/ 200 词（英文）时建议拆分
- **过渡词检查**：段落间需有逻辑过渡，但避免机械化连接词（"此外"、"另外"不得连续使用）

### 引用层

- **无引用事实陈述标记**：事实性陈述（非常识）缺少引用时标记 `[需引用]`
- **同一引用过度使用检测**：单篇文献在同一章节被引用 >3 次时发出警告
- **引用聚集检测**：单段引用 >5 篇时建议拆分段落或精简引用

---

## Step 4.3: 用户交互与迭代

### 草稿展示格式

```markdown
---
## 第2章 国内外研究现状（初稿）

**字数**: 2,347字 | **目标**: 2000-2500字 ✅
**引用文献**: 5篇

---

### 2.1 领域发展概述

[生成内容...]

### 2.2 代表性研究综述

[生成内容...]

---
**审阅选项**:
- [确认] 本章内容满意，继续下一章
- [修订] 提出具体修改意见
- [重写] 整章重新生成
- [调整] 修改篇幅/风格/深度
```

### 修订指令类型

| 指令 | 示例 | 处理方式 |
|------|------|----------|
| 扩写 | "2.2节再详细一些" | 增加细节，扩展篇幅 |
| 精简 | "背景部分太长了" | 压缩冗余，突出重点 |
| 换角度 | "从应用角度重写" | 调整论述视角 |
| 补充引用 | "加入XXX的工作" | 整合指定文献 |
| 调整语气 | "更正式一些" | 修改措辞风格 |
| 纠错 | "XX术语不准确" | 修正专业表述 |

### 迭代控制

```python
MAX_ITERATIONS = 5  # 单章最大迭代次数

for i in range(MAX_ITERATIONS):
    draft = generate_or_revise(chapter, feedback)
    show_draft(draft)
    feedback = get_user_feedback()

    if feedback.type == "confirm":
        mark_complete(chapter)
        break
    elif feedback.type == "skip":
        mark_pending(chapter)
        break

if i == MAX_ITERATIONS - 1:
    warn("已达最大迭代次数，建议手动编辑后继续")
```

---

## Step 4.4: 引用规范

### 引用格式

根据学科选择引用格式：

| 学科 | 格式 | 示例 |
|------|------|------|
| 计算机/AI | 数字标注 | [1], [2-4] |
| 生物医学 | 作者-年份 | (Wang et al., 2023) |
| 经管 | APA | Wang & Li (2023) |
| 通用 | 数字标注 | [1] |

### 引用嵌入规则

```markdown
✅ 正确：
近年来，图神经网络在XX领域取得显著进展[1,2]。Wang等人[3]提出了...

❌ 错误：
近年来，图神经网络取得显著进展。（无引用支撑）
```

### 引用密度建议

| 章节类型 | 建议密度 |
|----------|----------|
| 研究现状 | 高（每段1-3处） |
| 技术路线 | 中（关键方法处） |
| 选题背景 | 中（事实陈述处） |
| 创新点 | 低（对比处） |

---

## Step 4.5: 输出与存档

### 章节输出

每章确认后保存至 `drafts/` 目录：

```
research-scaffold/
└── drafts/
    ├── chapter-1-background.md
    ├── chapter-2-literature.md
    ├── chapter-3-content.md
    ├── chapter-4-methodology.md
    ├── chapter-5-feasibility.md
    ├── chapter-6-innovation.md
    └── chapter-7-schedule.md
```

### 章节文件格式

```markdown
---
chapter: 2
title: 国内外研究现状
status: completed
word_count: 2347
iterations: 2
last_modified: {timestamp}
---

# 2. 国内外研究现状

## 2.1 领域发展概述

[正文内容...]

## 2.2 代表性研究综述

[正文内容...]

---

## 引用列表

[1] Wang et al., "Title...", Venue, 2023.
[2] ...
```

### 全文汇总

所有章节完成后，生成完整报告：

```
research-scaffold/
└── output/
    ├── proposal-draft.md      # 完整报告（Markdown）
    ├── proposal-draft.docx    # Word格式（可选）
    └── references.bib         # 引用文献
```

---

## Step 4.6: 一致性检查

全文生成后执行：

| 检查项 | 说明 |
|--------|------|
| 术语一致 | 同一概念使用相同术语 |
| 引用连续 | 引用编号按出现顺序 |
| 篇幅平衡 | 各章比例符合大纲 |
| 逻辑衔接 | 章节间过渡自然 |
| 格式统一 | 标题、段落格式一致 |
| 引用完整性 | 无 [@TODO] 占位、无悬空引用、无遗漏关键文献 |
| 限定词审计 | "显著"、"重要"等高频限定词密度 ≤3次/千字 |
| 缩写一致性 | 缩写首次出现有完整定义，后续使用统一 |
| 数值一致性 | 摘要/正文/图表中同一数据点数值一致 |
| AI 痕迹扫描 | 扫描禁用表达列表，标记需人工替换的位置 |

```python
def consistency_check(full_report):
    issues = []
    issues += check_terminology(full_report)
    issues += check_citations(full_report)
    issues += check_word_count(full_report, outline)
    issues += check_transitions(full_report)
    issues += check_citation_integrity(full_report)   # 新增
    issues += check_qualifier_density(full_report)     # 新增
    issues += check_abbreviations(full_report)         # 新增
    issues += check_numeric_consistency(full_report)   # 新增
    issues += check_ai_traces(full_report)             # 新增

    if issues:
        show_issues(issues)
        offer_auto_fix()
    else:
        mark_complete()
```

---

## 异常处理

| 情况 | 处理策略 |
|------|----------|
| 文献不足 | 提示用户补充，或标记为"待引用" |
| 篇幅超限 | 自动建议精简段落 |
| 用户跳过章节 | 保存当前状态，允许后续补充 |
| 迭代过多 | 建议手动编辑，提供编辑入口 |
| 术语冲突 | 列出冲突项，用户选择统一用法 |

---

## 与最终交付衔接

Step 4 完成后输出：
- `output/proposal-draft.md` - 完整开题报告
- `output/references.bib` - 引用文献库

用户可进行最终人工润色后提交。
