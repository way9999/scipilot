"""SciPilot Python sidecar — FastAPI wrapper around tools/*."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def _resource_root() -> Path:
    env_root = os.environ.get("SCIPILOT_RESOURCE_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[2]


RESOURCE_ROOT = _resource_root()
TOOLS_ROOT = RESOURCE_ROOT / "tools"
SCIPILOT_ROOT = RESOURCE_ROOT / "scipilot"

for candidate in [RESOURCE_ROOT, SCIPILOT_ROOT]:
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from sidecar.routers import experiment, landscape, llm, search, state, writing  # noqa: E402

app = FastAPI(title="SciPilot Sidecar", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router, prefix="/api")
app.include_router(state.router, prefix="/api")
app.include_router(experiment.router, prefix="/api")
app.include_router(landscape.router, prefix="/api")
app.include_router(llm.router, prefix="/api")
app.include_router(writing.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok", "resource_root": str(RESOURCE_ROOT)}


def main():
    # Required for multiprocessing spawn in the frozen PyInstaller sidecar on Windows.
    mp.freeze_support()

    parser = argparse.ArgumentParser(description="SciPilot sidecar server")
    parser.add_argument("--port", type=int, default=9960)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
