# 图表规范指南 (Figure & Table Guide)

> 论文图表的选择、制作与引用规范，确保图表质量达到投稿标准。

## 图表类型选择矩阵

根据数据类型和展示目的选择合适的图表：

| 数据/目的 | 推荐图表 | 工具函数 |
|-----------|----------|----------|
| 方法对比（多指标） | 分组柱状图 | `plot_comparison_bar()` |
| 消融实验 | 热力图 | `plot_ablation_heatmap()` |
| 训练过程 | 折线图 | `plot_training_curve()` |
| 分类性能 | 混淆矩阵 | `plot_confusion_matrix()` |
| 数据分布 | 小提琴图/箱线图 | `plot_distribution()` |
| 相关性分析 | 散点图+回归线 | `plot_scatter_with_regression()` |
| 多维能力对比 | 雷达图 | `plot_radar()` |
| 系统架构 | 框图 | 手动绘制（draw.io/TikZ） |
| 流程/管道 | 流程图 | 手动绘制或 Mermaid |

## 图表制作规范

### 通用要求

- **格式**：矢量图优先（PDF/SVG），位图至少 300 DPI
- **配色**：使用色盲友好配色方案（`figure_generator.py` 默认使用 tableau-colorblind10）
- **字号**：图中文字 ≥ 8pt，与正文字号协调
- **图例**：放在图内不遮挡数据的位置，或图下方
- **坐标轴**：标签完整（含单位），刻度合理，不截断数据

### Caption 写作规范

- Caption 必须**独立可读**：不看正文也能理解图表内容
- 结构：**描述 + 关键发现**
- 必须包含关键数值（如最佳结果、提升幅度）
- 子图需逐一说明：(a) ..., (b) ...

示例：
```
✅ Figure 3. Comparison of five methods on CIFAR-10. Our method achieves
   95.2% accuracy, outperforming the best baseline (ResNet-50, 93.8%) by 1.4%.

❌ Figure 3. Results.
❌ Figure 3. This figure shows the comparison results of different methods.
```

### 表格格式规范

- 使用**三线表**（booktabs）：`\toprule`, `\midrule`, `\bottomrule`
- 数值**右对齐**或**小数点对齐**
- 最佳结果**加粗**（`format_results_table(bold_best=True)`）
- 显著性标注：`*` (p<0.05), `**` (p<0.01), `***` (p<0.001)
- 均值±标准差格式：`85.2 ± 1.3`
- 表格 caption 放在表格**上方**（与图不同）

示例：
```latex
\begin{table}[htbp]
\centering
\caption{Performance comparison on three benchmarks. Best results in \textbf{bold}.}
\label{tab:main-results}
\begin{tabular}{lccc}
\toprule
Method & CIFAR-10 & CIFAR-100 & ImageNet \\
\midrule
ResNet-50 & 93.8 ± 0.2 & 76.5 ± 0.3 & 76.1 ± 0.1 \\
ViT-B/16  & 94.1 ± 0.3 & 77.2 ± 0.4 & 77.8 ± 0.2 \\
Ours      & \textbf{95.2 ± 0.2} & \textbf{78.9 ± 0.3} & \textbf{79.3 ± 0.1} \\
\bottomrule
\end{tabular}
\end{table}
```

## 图表引用规范

- 正文中**必须引用**每一张图表，不得出现未引用的图表
- 引用格式：`如图~\ref{fig:xxx}所示` / `As shown in Figure~\ref{fig:xxx}`
- 编号必须**连续**，按首次引用顺序排列
- 图表应紧跟首次引用的段落（使用 `[htbp]` 浮动选项）

## 图表数量建议

| 论文类型 | 建议图表数 | 说明 |
|----------|-----------|------|
| 会议短文（4页） | 3-4 张 | 1 架构图 + 1 主结果表 + 1-2 分析图 |
| 会议长文（8页） | 5-7 张 | 1 架构图 + 2 结果表 + 2-4 分析图 |
| 期刊论文 | 6-10 张 | 1 架构图 + 2-3 结果表 + 3-6 分析图 |
| 毕业论文 | 10-20 张 | 按章节分配，每章 2-4 张 |

## 常见图表错误清单

| # | 错误 | 修正 |
|---|------|------|
| 1 | 位图模糊（截图粘贴） | 使用矢量图（PDF/SVG）或 300+ DPI PNG |
| 2 | 配色不友好（红绿对比） | 使用色盲友好配色方案 |
| 3 | 坐标轴无标签/无单位 | 补充完整标签和单位 |
| 4 | Caption 过于简略 | 写独立可读的描述+关键数值 |
| 5 | 表格无三线表格式 | 使用 booktabs 宏包 |
| 6 | 最佳结果未加粗 | 加粗最佳数值 |
| 7 | 图表未在正文引用 | 确保每张图表都被引用 |
| 8 | 图中字号过小 | 确保 ≥ 8pt |
| 9 | 数值精度不一致 | 同一表格统一小数位数 |
| 10 | 缺少误差线/标准差 | 多次实验需报告均值±标准差 |

## 自动化工作流

使用 `tools/figure_generator.py` 和 `tools/data_analyzer.py` 的推荐流程：

```python
from tools.data_analyzer import find_result_files, load_results, compute_metrics_summary, format_results_table
from tools.figure_generator import auto_figures_from_results, generate_figure_inventory

# 1. 发现结果文件
result_files = find_result_files("path/to/project")

# 2. 加载并分析
for f in result_files:
    df = load_results(f)
    summary = compute_metrics_summary(df, group_by="method")

# 3. 生成表格
table_latex = format_results_table(df, fmt="latex", bold_best=True,
    caption="Main results", label="tab:main-results")

# 4. 自动生成图表
figures = auto_figures_from_results("path/to/results", "output/figures")

# 5. 生成图表清单
generate_figure_inventory(figures, "drafts/figure-inventory.md")
```
