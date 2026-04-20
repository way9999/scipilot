from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCIPILOT_ROOT = ROOT / "scipilot"
SIDECAR_ROOT = SCIPILOT_ROOT / "sidecar"
SERVER_PY = SIDECAR_ROOT / "server.py"
DIST_DIR = SIDECAR_ROOT / "dist"
BUILD_DIR = SIDECAR_ROOT / "build"
SPEC_FILE = SIDECAR_ROOT / "scipilot-sidecar.spec"
EXE_NAME = "scipilot-sidecar"
EXECUTABLE_NAME = f"{EXE_NAME}.exe" if sys.platform == "win32" else EXE_NAME
TARGET_DIR = DIST_DIR / EXE_NAME
TARGET_EXE = TARGET_DIR / EXECUTABLE_NAME


def run(cmd: list[str]) -> None:
    print("[build_sidecar]", " ".join(str(part) for part in cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> None:
    if not SERVER_PY.exists():
        raise SystemExit(f"server.py not found: {SERVER_PY}")

    DIST_DIR.mkdir(parents=True, exist_ok=True)

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    if SPEC_FILE.exists():
        SPEC_FILE.unlink()
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)

    excludes = [
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "tkinter", "_tkinter",
        "IPython", "jupyter", "notebook", "nbformat", "sphinx", "docutils",
        "black", "yapf", "autopep8",
        "jedi", "parso",
        "zmq",
        "torch", "torchaudio", "torchvision", "transformers",
        "sklearn", "scipy", "pandas", "numpy", "matplotlib", "seaborn",
        "cv2", "PIL", "imageio", "skimage",
        "spacy", "thinc", "nltk",
        "datasets", "huggingface_hub",
        "selenium", "webdriver_manager",
        "dask", "distributed", "bokeh", "plotly", "altair",
        "numba", "llvmlite",
        "pyarrow", "tables", "h5py",
        "sqlalchemy", "psycopg2",
        "boto3", "botocore",
        "google", "grpc", "opentelemetry",
        "librosa", "soundfile", "resampy",
        "timm", "onnxruntime",
        "xarray", "statsmodels", "patsy",
        "nacl", "bcrypt", "cryptography",
        "win32com", "pythoncom", "pywintypes",
        "tqdm",
    ]

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        EXE_NAME,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(SIDECAR_ROOT),
        "--paths",
        str(ROOT),
        "--collect-submodules",
        "sidecar",
        "--collect-submodules",
        "tools",
    ]
    for pkg in excludes:
        cmd.extend(["--exclude", pkg])
    cmd.append(str(SERVER_PY))
    run(cmd)

    if not TARGET_EXE.exists():
        raise SystemExit(f"sidecar executable not produced: {TARGET_EXE}")

    print(f"[build_sidecar] built {TARGET_EXE}")


if __name__ == "__main__":
    main()
