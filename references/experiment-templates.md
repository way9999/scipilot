# 实验设计模板

> 根据学科方向提供实验设计引导，配合 `tools/experiment_design.py` 使用。

---

## 计算机科学 / AI

### 典型实验结构

```
1. 基线对比实验（必做）
   - 选择 3-5 个 SOTA 基线
   - 在 2-3 个公开数据集上对比
   - 报告主指标 + 辅助指标

2. 消融实验（必做）
   - 逐一移除/替换核心组件
   - 验证每个设计选择的贡献

3. 超参数敏感性分析（推荐）
   - 关键超参数的影响曲线
   - 拉丁超立方采样或网格搜索

4. 效率分析（推荐）
   - 训练时间 / 推理时间
   - 参数量 / FLOPs / 显存占用

5. 可视化分析（按需）
   - 注意力热力图 / 特征分布 t-SNE
   - 案例分析（成功 + 失败样例）
```

### 常用评价指标

| 任务类型 | 主指标 | 辅助指标 |
|----------|--------|----------|
| 分类 | Accuracy, F1 | Precision, Recall, AUC-ROC |
| 检测 | mAP, AP50 | IoU, FPS |
| 生成 | BLEU, ROUGE | BERTScore, Human Eval |
| 推荐 | NDCG, HR@K | MRR, AUC |
| 回归 | RMSE, MAE | R², MAPE |
| 分割 | mIoU, Dice | Pixel Acc, Hausdorff |

### 常用公开数据集参考

| 领域 | 数据集 |
|------|--------|
| NLP | GLUE, SuperGLUE, SQuAD, MMLU |
| CV | ImageNet, COCO, ADE20K, Cityscapes |
| 图学习 | Cora, PubMed, OGB, QM9 |
| 推荐 | MovieLens, Amazon Review, Yelp |
| 多模态 | LAION, VQA, Flickr30k |

### 实验设计工具调用示例

```python
from tools.experiment_design import ablation_study, baseline_comparison, hyperparameter_lhs

# 1. 消融实验
exps = ablation_study({
    "attention": ["multi-head", "single-head", "none"],
    "ffn": ["gelu", "relu", "none"],
    "norm": ["layernorm", "rmsnorm"],
})

# 2. 基线对比矩阵
matrix = baseline_comparison(
    method_name="Ours",
    baselines=["Transformer", "GNN", "MLP-Mixer"],
    datasets=["CIFAR-100", "ImageNet-1K"],
    metrics=["Top-1 Acc", "Top-5 Acc", "Params(M)", "FLOPs(G)"],
)

# 3. 超参数搜索（拉丁超立方）
configs = hyperparameter_lhs(
    params={"lr": (1e-5, 1e-2), "weight_decay": (0, 0.1), "dropout": (0, 0.5)},
    n_samples=30,
)
```

---

## 医药学

### 典型实验结构

#### 药物筛选 / 体外实验

```
1. 初筛（Primary Screening）
   - 单浓度（通常 10μM）筛选化合物库
   - 96/384 孔板，设阳性/阴性对照
   - 命中标准：抑制率 > 50%

2. 剂量-反应（Dose-Response）
   - 命中化合物做 8-10 个浓度梯度
   - 计算 IC50/EC50
   - 至少 3 次生物学重复

3. 选择性验证
   - 在相关靶点/细胞系上测试
   - 排除非特异性毒性（MTT/CCK-8）

4. 机制验证
   - Western Blot / qPCR 验证通路
   - 时间梯度实验
```

#### 临床前 / 动物实验

```
1. 分组设计
   - 对照组（Vehicle）+ 阳性药物组 + 实验组（2-3个剂量）
   - 每组 ≥ 6 只（统计效力要求）
   - 随机分组 + 盲法

2. 给药方案
   - 给药途径、频率、周期
   - 药代动力学参数（如已知）

3. 终点指标
   - 主要终点：疗效指标
   - 次要终点：安全性指标（体重、血常规、脏器系数）
   - 生存分析（如适用）
```

#### AI + 医药交叉

```
1. 数据集构建
   - 公开数据库：ChEMBL, PubChem, DrugBank, TCGA, GEO
   - 数据划分：scaffold split（药物）/ patient split（临床）
   - 避免数据泄露（时间截断 / 结构相似性）

2. 模型评估
   - 分子性质预测：AUROC, AUPRC, Enrichment Factor
   - 药物-靶点：Hit Rate@K, BEDROC
   - 生存预测：C-index, Time-dependent AUC
   - 分子生成：Validity, Uniqueness, Novelty, QED, SA Score

3. 基线方法
   - 传统 ML：RF, XGBoost, SVM
   - 图神经网络：GCN, GAT, MPNN, SchNet
   - 预训练模型：MolBERT, ChemBERTa, Uni-Mol
   - 生成模型：VAE, Flow, Diffusion
```

### 常用评价指标

| 实验类型 | 指标 |
|----------|------|
| 细胞活性 | IC50, EC50, CC50, SI (选择性指数) |
| 动物实验 | 肿瘤抑制率(TGI), 生存期(OS), 体重变化 |
| 临床试验 | ORR, PFS, OS, HR, p-value |
| AI药物发现 | AUROC, EF1%, Hit Rate, BEDROC |
| 分子生成 | Validity, Uniqueness, Novelty, FCD |

### 实验设计工具调用示例

```python
from tools.experiment_design import dose_response, screening_plate, clinical_groups

# 1. 剂量-反应实验
groups = dose_response(
    doses=[0.01, 0.1, 1, 10, 100],  # μM
    replicates=3,
    include_control=True,
)

# 2. 96孔板筛选布局
plate = screening_plate(
    compounds=["Compound_A", "Compound_B", "Compound_C"],
    concentrations=[0.1, 1, 10, 100],
    replicates=2,
    plate_size=96,
)

# 3. 动物实验分组
design = clinical_groups(
    arms=["Vehicle", "Drug_Low", "Drug_High", "Positive_Control"],
    n_per_arm=8,
    stratify_by=["weight", "tumor_volume"],
)
```

---

## 统计学注意事项

- 样本量计算：实验前用 power analysis 确定最小样本量
- 多重比较校正：多组对比时用 Bonferroni 或 FDR 校正
- 随机化：所有分组实验必须随机化，避免系统偏差
- 重复性：生物学重复 ≥ 3 次，技术重复 ≥ 2 次
