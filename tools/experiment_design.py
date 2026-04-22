"""实验设计工具
整合 pyDOE3 + Optuna + TDC，提供 CS/AI 和医药学完整实验设计能力。
安装: pip install pyDOE3 optuna PyTDC
"""

import json
import sys
import os
from itertools import product

try:
    import pyDOE3 as doe
    import numpy as np
    HAS_DOE = True
except ImportError:
    HAS_DOE = False

try:
    import optuna
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False

try:
    import tdc
    HAS_TDC = True
except ImportError:
    HAS_TDC = False


# ── CS/AI 实验设计 ────────────────────────────────────────────────────

def ablation_study(components: dict[str, list]) -> list[dict]:
    """消融实验设计：逐一移除/替换组件，观察性能变化

    Args:
        components: 组件名→可选值列表，第一个值为默认(完整模型)
            例: {"attention": ["self-attn", "none"],
                 "norm": ["layernorm", "batchnorm", "none"],
                 "dropout": [0.1, 0.0]}

    Returns:
        实验配置列表，包含 full model + 每个组件的变体
    """
    # 完整模型（所有组件取默认值）
    full = {k: v[0] for k, v in components.items()}
    experiments = [{"name": "Full Model", "config": dict(full)}]

    # 逐一替换每个组件
    for comp, values in components.items():
        for alt in values[1:]:
            config = dict(full)
            config[comp] = alt
            label = f"w/o {comp}" if alt in ("none", None, 0, 0.0, False) else f"{comp}={alt}"
            experiments.append({"name": label, "config": config})

    return experiments


def hyperparameter_grid(params: dict[str, list]) -> list[dict]:
    """网格搜索设计：全因子组合

    Args:
        params: 超参数名→候选值列表
            例: {"lr": [1e-3, 1e-4, 1e-5], "batch_size": [16, 32, 64]}
    """
    keys = list(params.keys())
    combos = list(product(*params.values()))
    return [dict(zip(keys, combo)) for combo in combos]


def hyperparameter_lhs(params: dict[str, tuple[float, float]],
                       n_samples: int = 20) -> list[dict]:
    """拉丁超立方采样：在连续空间中高效采样

    Args:
        params: 超参数名→(最小值, 最大值)
            例: {"lr": (1e-5, 1e-2), "weight_decay": (0, 0.1)}
        n_samples: 采样数量
    """
    if not HAS_DOE:
        raise ImportError("需要安装 pyDOE3: pip install pyDOE3")

    keys = list(params.keys())
    bounds = list(params.values())
    n_factors = len(keys)

    lhs_matrix = doe.lhs(n_factors, samples=n_samples, criterion="maximin")

    experiments = []
    for row in lhs_matrix:
        config = {}
        for i, key in enumerate(keys):
            low, high = bounds[i]
            config[key] = round(low + row[i] * (high - low), 6)
        experiments.append(config)

    return experiments


def baseline_comparison(method_name: str, baselines: list[str],
                        datasets: list[str],
                        metrics: list[str]) -> dict:
    """基线对比实验矩阵

    Args:
        method_name: 你的方法名
        baselines: 基线方法列表
        datasets: 数据集列表
        metrics: 评价指标列表

    Returns:
        实验矩阵描述
    """
    all_methods = [method_name] + baselines
    matrix = {
        "methods": all_methods,
        "datasets": datasets,
        "metrics": metrics,
        "total_runs": len(all_methods) * len(datasets),
        "table_template": _generate_result_table(all_methods, datasets, metrics),
    }
    return matrix


def _generate_result_table(methods, datasets, metrics) -> str:
    """生成结果表格 Markdown 模板"""
    header_metrics = " | ".join(metrics)
    lines = [f"| Method | Dataset | {header_metrics} |"]
    lines.append("|" + "---|" * (2 + len(metrics)))
    for ds in datasets:
        for m in methods:
            cells = " | ".join(["—"] * len(metrics))
            lines.append(f"| {m} | {ds} | {cells} |")
    return "\n".join(lines)


# ── 医药学实验设计 ────────────────────────────────────────────────────

def dose_response(doses: list[float], replicates: int = 3,
                  include_control: bool = True) -> list[dict]:
    """剂量-反应实验设计

    Args:
        doses: 剂量梯度列表（如 [0.1, 1, 10, 100] μM）
        replicates: 每个剂量的重复次数
        include_control: 是否包含空白对照
    """
    groups = []
    if include_control:
        groups.append({
            "group": "Vehicle Control",
            "dose": 0,
            "n": replicates,
            "purpose": "基线对照",
        })

    for dose in doses:
        groups.append({
            "group": f"Dose {dose}",
            "dose": dose,
            "n": replicates,
            "purpose": "剂量效应",
        })

    return groups


def clinical_groups(arms: list[str], n_per_arm: int,
                    stratify_by: list[str] | None = None) -> dict:
    """临床/临床前分组设计

    Args:
        arms: 实验组列表，如 ["Treatment A", "Treatment B", "Placebo"]
        n_per_arm: 每组样本量
        stratify_by: 分层因素，如 ["age", "sex"]
    """
    design = {
        "arms": arms,
        "n_per_arm": n_per_arm,
        "total_n": len(arms) * n_per_arm,
        "stratification": stratify_by or [],
    }

    if stratify_by:
        design["note"] = f"按 {', '.join(stratify_by)} 分层随机化，确保各组基线均衡"

    return design


def screening_plate(compounds: list[str], concentrations: list[float],
                    replicates: int = 2, plate_size: int = 96) -> dict:
    """药物筛选板布局设计（96/384孔板）

    Args:
        compounds: 化合物列表
        concentrations: 浓度梯度
        replicates: 重复次数
        plate_size: 孔板规格 (96 或 384)
    """
    wells_needed = len(compounds) * len(concentrations) * replicates
    # 留 8 孔做对照
    usable = plate_size - 8
    plates_needed = max(1, -(-wells_needed // usable))  # 向上取整

    return {
        "compounds": len(compounds),
        "concentrations": concentrations,
        "replicates": replicates,
        "wells_per_compound": len(concentrations) * replicates,
        "total_wells": wells_needed,
        "control_wells": 8 * plates_needed,
        "plates_needed": plates_needed,
        "plate_size": plate_size,
    }


def factorial_design(factors: dict[str, list], design_type: str = "full") -> list[dict]:
    """通用因子实验设计（适用于所有学科）

    Args:
        factors: 因子名→水平列表
        design_type: "full"(全因子) | "fractional"(分数因子) | "plackett-burman"(PB设计)
    """
    if not HAS_DOE:
        raise ImportError("需要安装 pyDOE3: pip install pyDOE3")

    keys = list(factors.keys())
    levels = [len(v) for v in factors.values()]

    if design_type == "full":
        combos = list(product(*factors.values()))
        return [dict(zip(keys, combo)) for combo in combos]

    elif design_type == "fractional":
        # 2-level fractional factorial
        n = len(keys)
        gen = " ".join(f"a{i}" for i in range(min(n, 7)))
        matrix = doe.fracfact(gen)
        experiments = []
        for row in matrix:
            config = {}
            for i, key in enumerate(keys):
                vals = factors[key]
                idx = 0 if row[i] < 0 else min(len(vals) - 1, 1)
                config[key] = vals[idx]
            experiments.append(config)
        return experiments

    elif design_type == "plackett-burman":
        n = len(keys)
        matrix = doe.pbdesign(n)
        experiments = []
        for row in matrix:
            config = {}
            for i, key in enumerate(keys):
                vals = factors[key]
                idx = 0 if row[i] < 0 else min(len(vals) - 1, 1)
                config[key] = vals[idx]
            experiments.append(config)
        return experiments

    return []


# ── Optuna 超参优化 ───────────────────────────────────────────────────

def create_optuna_study(study_name: str, direction: str = "maximize",
                        storage: str | None = None) -> "optuna.Study":
    """创建 Optuna 优化研究

    Args:
        study_name: 研究名称
        direction: "maximize" 或 "minimize"
        storage: 可选，SQLite 持久化路径，如 "sqlite:///experiments.db"
    """
    if not HAS_OPTUNA:
        raise ImportError("需要安装 optuna: pip install optuna")

    return optuna.create_study(
        study_name=study_name,
        direction=direction,
        storage=storage,
        load_if_exists=True,
    )


def suggest_search_space(trial, params: dict) -> dict:
    """根据参数定义自动生成 Optuna 搜索空间

    Args:
        trial: Optuna trial 对象
        params: 参数定义字典，格式：
            {"lr": ("log_float", 1e-5, 1e-1),
             "hidden_dim": ("int", 64, 512),
             "dropout": ("float", 0.0, 0.5),
             "optimizer": ("categorical", ["adam", "sgd", "adamw"])}
    """
    config = {}
    for name, spec in params.items():
        ptype = spec[0]
        if ptype == "log_float":
            config[name] = trial.suggest_float(name, spec[1], spec[2], log=True)
        elif ptype == "float":
            config[name] = trial.suggest_float(name, spec[1], spec[2])
        elif ptype == "int":
            config[name] = trial.suggest_int(name, spec[1], spec[2])
        elif ptype == "categorical":
            config[name] = trial.suggest_categorical(name, spec[1])
    return config


def optuna_search_template(params: dict, n_trials: int = 50,
                           direction: str = "maximize") -> str:
    """生成 Optuna 超参搜索的完整代码模板

    Args:
        params: 参数定义（同 suggest_search_space 格式）
        n_trials: 搜索次数
        direction: 优化方向

    Returns:
        可直接运行的 Python 代码字符串
    """
    param_lines = []
    for name, spec in params.items():
        ptype = spec[0]
        if ptype == "log_float":
            param_lines.append(f'        "{name}": trial.suggest_float("{name}", {spec[1]}, {spec[2]}, log=True),')
        elif ptype == "float":
            param_lines.append(f'        "{name}": trial.suggest_float("{name}", {spec[1]}, {spec[2]}),')
        elif ptype == "int":
            param_lines.append(f'        "{name}": trial.suggest_int("{name}", {spec[1]}, {spec[2]}),')
        elif ptype == "categorical":
            param_lines.append(f'        "{name}": trial.suggest_categorical("{name}", {spec[1]}),')

    params_block = "\n".join(param_lines)

    return f'''import optuna

def objective(trial):
    config = {{
{params_block}
    }}

    # TODO: 用 config 训练模型，返回评价指标
    # model = build_model(**config)
    # score = train_and_evaluate(model)
    # return score
    pass

study = optuna.create_study(direction="{direction}")
study.optimize(objective, n_trials={n_trials})

print("Best params:", study.best_params)
print("Best value:", study.best_value)

# 可视化（可选）
# from optuna.visualization import plot_optimization_history, plot_param_importances
# plot_optimization_history(study).show()
# plot_param_importances(study).show()
'''


def get_optuna_results(study) -> dict:
    """提取 Optuna 研究结果摘要"""
    if not HAS_OPTUNA:
        raise ImportError("需要安装 optuna: pip install optuna")

    return {
        "best_params": study.best_params,
        "best_value": study.best_value,
        "n_trials": len(study.trials),
        "trials_summary": [
            {"number": t.number, "value": t.value, "params": t.params}
            for t in study.trials if t.value is not None
        ],
    }


# ── TDC 医药数据集与基准 ──────────────────────────────────────────────

def list_tdc_benchmarks() -> dict:
    """列出 TDC 可用的基准测试组"""
    return {
        "admet": {
            "name": "ADMET Benchmark Group",
            "description": "药物吸收、分布、代谢、排泄、毒性",
            "tasks": ["Caco2_Wang", "HIA_Hou", "Pgp_Broccatelli", "BBB_Martins",
                      "CYP2D6_Veith", "CYP3A4_Veith", "CYP2C9_Veith",
                      "hERG", "AMES", "DILI", "LD50_Zhu"],
            "usage": 'from tdc.benchmark_group import admet_group\ngroup = admet_group(path="data/")',
        },
        "drugcombo": {
            "name": "DrugCombo Benchmark",
            "description": "药物组合协同效应预测",
            "usage": 'from tdc.benchmark_group import drugcombo_group\ngroup = drugcombo_group(path="data/")',
        },
        "dti": {
            "name": "Drug-Target Interaction",
            "description": "药物-靶点相互作用预测",
            "tasks": ["DAVIS", "KIBA", "BindingDB_Kd"],
        },
        "docking": {
            "name": "Docking Benchmark",
            "description": "分子对接评估",
            "usage": 'from tdc.benchmark_group import docking_group\ngroup = docking_group(path="data/")',
        },
    }


def load_tdc_dataset(task_name: str, dataset_name: str,
                     path: str = "data/") -> dict:
    """加载 TDC 数据集

    Args:
        task_name: 任务类型，如 "ADME", "Tox", "DTI", "DrugSyn"
        dataset_name: 数据集名，如 "Caco2_Wang", "DAVIS"
        path: 数据缓存目录

    Returns:
        {"train": df, "valid": df, "test": df, "info": {...}}
    """
    if not HAS_TDC:
        raise ImportError("需要安装 PyTDC: pip install PyTDC")

    # 动态导入对应任务类
    task_map = {
        "ADME": ("tdc.single_pred", "ADME"),
        "Tox": ("tdc.single_pred", "Tox"),
        "DTI": ("tdc.multi_pred", "DTI"),
        "DDI": ("tdc.multi_pred", "DDI"),
        "DrugSyn": ("tdc.multi_pred", "DrugSyn"),
        "MolGen": ("tdc.generation", "MolGen"),
    }

    if task_name not in task_map:
        raise ValueError(f"未知任务: {task_name}，可选: {list(task_map.keys())}")

    module_path, class_name = task_map[task_name]
    import importlib
    module = importlib.import_module(module_path)
    TaskClass = getattr(module, class_name)

    data = TaskClass(name=dataset_name, path=path)
    split = data.get_split()

    return {
        "train": split["train"],
        "valid": split["valid"],
        "test": split["test"],
        "info": {
            "task": task_name,
            "dataset": dataset_name,
            "train_size": len(split["train"]),
            "valid_size": len(split["valid"]),
            "test_size": len(split["test"]),
        },
    }


def tdc_evaluate(predictions: list, labels: list, task_type: str = "binary") -> dict:
    """使用 TDC 评估器计算指标

    Args:
        predictions: 模型预测值
        labels: 真实标签
        task_type: "binary"(分类) | "regression"
    """
    if not HAS_TDC:
        raise ImportError("需要安装 PyTDC: pip install PyTDC")

    from tdc import Evaluator

    results = {}
    if task_type == "binary":
        for metric in ["AUROC", "AUPRC", "F1"]:
            evaluator = Evaluator(name=metric)
            results[metric] = evaluator(predictions, labels)
    elif task_type == "regression":
        for metric in ["MAE", "RMSE", "Pearson", "Spearman"]:
            evaluator = Evaluator(name=metric)
            results[metric] = evaluator(predictions, labels)

    return results


def tdc_experiment_template(task_name: str, dataset_name: str) -> str:
    """生成 TDC 实验的完整代码模板"""
    return f'''from tdc.single_pred import {task_name}
from tdc import Evaluator

# 1. 加载数据
data = {task_name}(name="{dataset_name}", path="data/")
split = data.get_split()
train, valid, test = split["train"], split["valid"], split["test"]

print(f"Train: {{len(train)}}, Valid: {{len(valid)}}, Test: {{len(test)}}")
print(f"Columns: {{list(train.columns)}}")

# 2. TODO: 特征提取 + 模型训练
# from rdkit import Chem
# from rdkit.Chem import AllChem
# X_train = [AllChem.GetMorganFingerprintAsBitVect(Chem.MolFromSmiles(s), 2) for s in train["Drug"]]

# 3. 评估
evaluator = Evaluator(name="AUROC")
# score = evaluator(y_pred, y_true)
'''


# ── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== 消融实验示例 ===")
    exps = ablation_study({
        "attention": ["multi-head", "single-head", "none"],
        "residual": [True, False],
        "dropout": [0.1, 0.0],
    })
    for e in exps:
        print(f"  {e['name']}: {e['config']}")

    print("\n=== 拉丁超立方采样示例 ===")
    if HAS_DOE:
        samples = hyperparameter_lhs(
            {"lr": (1e-5, 1e-2), "weight_decay": (0, 0.1), "warmup_steps": (100, 2000)},
            n_samples=5,
        )
        for s in samples:
            print(f"  {s}")

    print("\n=== 剂量-反应示例 ===")
    groups = dose_response([0.1, 1, 10, 100], replicates=3)
    for g in groups:
        print(f"  {g['group']}: dose={g['dose']}, n={g['n']}")
