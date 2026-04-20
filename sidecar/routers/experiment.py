"""Experiment design endpoints — wraps tools/experiment_design.py (Phase 3)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tools.experiment_design import (
    ablation_study,
    baseline_comparison,
    clinical_groups,
    dose_response,
    factorial_design,
    hyperparameter_grid,
    hyperparameter_lhs,
    list_tdc_benchmarks,
    optuna_search_template,
    screening_plate,
    tdc_experiment_template,
)

router = APIRouter(prefix="/experiment", tags=["experiment"])


# ── Request models ───────────────────────────────────────────────────


class AblationRequest(BaseModel):
    """Ablation study: map component name to list of variants (first = default)."""
    components: dict[str, list[Any]]


class BaselineRequest(BaseModel):
    proposed: str
    baselines: list[str]
    datasets: list[str]
    metrics: list[str]


class GridRequest(BaseModel):
    params: dict[str, list[Any]]


class LHSRequest(BaseModel):
    params: dict[str, list[float]] = Field(
        ..., description="Map param name to [min, max]."
    )
    n_samples: int = Field(default=20, ge=1, le=10000)


class DoseResponseRequest(BaseModel):
    concentrations: list[float]
    replicates: int = Field(default=3, ge=1)
    include_control: bool = True


class ScreeningPlateRequest(BaseModel):
    compounds: list[str]
    concentrations: list[float]
    replicates: int = Field(default=2, ge=1)
    plate_size: int = Field(default=96, description="96 or 384")


class ClinicalGroupsRequest(BaseModel):
    arms: list[str]
    n_per_arm: int = Field(ge=1)
    stratify_by: list[str] | None = None


class FactorialRequest(BaseModel):
    factors: dict[str, list[Any]]
    design_type: str = Field(
        default="full",
        description="full | fractional | plackett-burman",
    )


class OptunaTemplateRequest(BaseModel):
    """Each value is [type_str, ...args], e.g. ["log_float", 1e-5, 1e-1]."""
    search_space: dict[str, list[Any]]
    n_trials: int = Field(default=50, ge=1)
    direction: str = Field(default="maximize", description="maximize | minimize")


class TDCTemplateRequest(BaseModel):
    benchmark_group: str = Field(..., description="TDC task name, e.g. ADME, Tox")
    dataset_name: str = Field(..., description="Dataset name, e.g. Caco2_Wang")


# ── Helpers ──────────────────────────────────────────────────────────


def _lhs_params_to_tuples(params: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    """Convert {name: [min, max]} to {name: (min, max)} expected by the tool."""
    result = {}
    for k, v in params.items():
        if len(v) != 2:
            raise ValueError(
                f"LHS param '{k}' must have exactly 2 elements [min, max], got {len(v)}."
            )
        result[k] = (v[0], v[1])
    return result


def _optuna_space_to_tuples(space: dict[str, list[Any]]) -> dict[str, tuple]:
    """Convert JSON lists to tuples expected by optuna_search_template."""
    return {k: tuple(v) for k, v in space.items()}


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/health")
async def experiment_health():
    return {"status": "ok", "message": "Experiment endpoints are live."}


@router.post("/ablation")
async def ablation(req: AblationRequest):
    """Design an ablation study — one component varied at a time."""
    try:
        experiments = await asyncio.to_thread(ablation_study, req.components)
        return {
            "success": True,
            "data": {
                "experiment_count": len(experiments),
                "experiments": experiments,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/baseline")
async def baseline(req: BaselineRequest):
    """Generate a baseline comparison matrix."""
    try:
        matrix = await asyncio.to_thread(
            baseline_comparison,
            req.proposed,
            req.baselines,
            req.datasets,
            req.metrics,
        )
        return {"success": True, "data": matrix}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hyperparameter-grid")
async def hp_grid(req: GridRequest):
    """Full-factorial grid search over discrete parameter values."""
    try:
        combos = await asyncio.to_thread(hyperparameter_grid, req.params)
        return {
            "success": True,
            "data": {
                "combination_count": len(combos),
                "combinations": combos,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hyperparameter-lhs")
async def hp_lhs(req: LHSRequest):
    """Latin Hypercube Sampling in continuous parameter space."""
    try:
        param_tuples = _lhs_params_to_tuples(req.params)
        samples = await asyncio.to_thread(
            hyperparameter_lhs, param_tuples, req.n_samples
        )
        return {
            "success": True,
            "data": {
                "sample_count": len(samples),
                "samples": samples,
            },
        }
    except (ImportError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dose-response")
async def dose_response_endpoint(req: DoseResponseRequest):
    """Design a dose-response experiment with optional vehicle control."""
    try:
        groups = await asyncio.to_thread(
            dose_response,
            req.concentrations,
            replicates=req.replicates,
            include_control=req.include_control,
        )
        return {
            "success": True,
            "data": {
                "group_count": len(groups),
                "groups": groups,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/screening-plate")
async def screening_plate_endpoint(req: ScreeningPlateRequest):
    """Layout a screening plate (96- or 384-well)."""
    try:
        layout = await asyncio.to_thread(
            screening_plate,
            req.compounds,
            req.concentrations,
            replicates=req.replicates,
            plate_size=req.plate_size,
        )
        return {"success": True, "data": layout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clinical-groups")
async def clinical_groups_endpoint(req: ClinicalGroupsRequest):
    """Design clinical / pre-clinical arm groups with optional stratification."""
    try:
        design = await asyncio.to_thread(
            clinical_groups,
            req.arms,
            req.n_per_arm,
            stratify_by=req.stratify_by,
        )
        return {"success": True, "data": design}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/factorial")
async def factorial_endpoint(req: FactorialRequest):
    """General factorial design (full, fractional, or Plackett-Burman)."""
    try:
        experiments = await asyncio.to_thread(
            factorial_design, req.factors, req.design_type
        )
        return {
            "success": True,
            "data": {
                "design_type": req.design_type,
                "experiment_count": len(experiments),
                "experiments": experiments,
            },
        }
    except (ImportError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optuna-template")
async def optuna_template(req: OptunaTemplateRequest):
    """Generate a ready-to-run Optuna hyperparameter search script."""
    try:
        params = _optuna_space_to_tuples(req.search_space)
        code = await asyncio.to_thread(
            optuna_search_template, params, req.n_trials, req.direction
        )
        return {"success": True, "data": {"code": code}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tdc-benchmarks")
async def tdc_benchmarks():
    """List available TDC benchmark groups and their datasets."""
    try:
        benchmarks = await asyncio.to_thread(list_tdc_benchmarks)
        return {"success": True, "data": benchmarks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tdc-template")
async def tdc_template(req: TDCTemplateRequest):
    """Generate a TDC experiment code template for a given benchmark/dataset."""
    try:
        code = await asyncio.to_thread(
            tdc_experiment_template, req.benchmark_group, req.dataset_name
        )
        return {"success": True, "data": {"code": code}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
