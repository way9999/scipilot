from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = PROJECT_ROOT / "src-tauri" / "target" / "release" / "bundle"


@dataclass(frozen=True)
class ArtifactSpec:
    key: str
    files: tuple[str, ...]
    signature: str
    url_name: str


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print("[build_release.py]", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True, env=env)


def load_package_version() -> str:
    package_json = json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))
    return str(package_json["version"]).strip()


def normalize_arch() -> str:
    raw = platform.machine().lower()
    return {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
        "armv7l": "armv7",
        "armv7": "armv7",
    }.get(raw, raw)


def current_target() -> str:
    if sys.platform.startswith("win"):
        os_name = "windows"
    elif sys.platform == "darwin":
        os_name = "darwin"
    elif sys.platform.startswith("linux"):
        os_name = "linux"
    else:
        raise SystemExit(f"Unsupported platform: {sys.platform}")
    return f"{os_name}-{normalize_arch()}"


def discover_artifacts(target: str, version: str) -> list[ArtifactSpec]:
    if target.startswith("windows-"):
        msi = f"SciPilot_{version}_x64_en-US.msi"
        nsis = f"SciPilot_{version}_x64-setup.exe"
        return [
            ArtifactSpec(
                key="windows-x86_64",
                files=("msi/" + msi, "msi/" + msi + ".sig"),
                signature="msi/" + msi + ".sig",
                url_name=msi,
            ),
            ArtifactSpec(
                key="windows-x86_64-msi",
                files=("msi/" + msi, "msi/" + msi + ".sig"),
                signature="msi/" + msi + ".sig",
                url_name=msi,
            ),
            ArtifactSpec(
                key="windows-x86_64-nsis",
                files=("nsis/" + nsis, "nsis/" + nsis + ".sig"),
                signature="nsis/" + nsis + ".sig",
                url_name=nsis,
            ),
        ]

    if target.startswith("linux-"):
        appimages = sorted((BUNDLE_ROOT / "appimage").glob("*.AppImage"))
        if not appimages:
            raise SystemExit("No AppImage artifact found under src-tauri/target/release/bundle/appimage")
        appimage = appimages[0]
        signature = appimage.with_suffix(appimage.suffix + ".sig")
        if not signature.exists():
            raise SystemExit(f"Missing AppImage signature: {signature}")
        rel_appimage = appimage.relative_to(BUNDLE_ROOT).as_posix()
        rel_sig = signature.relative_to(BUNDLE_ROOT).as_posix()
        return [
            ArtifactSpec(
                key=target,
                files=(rel_appimage, rel_sig),
                signature=rel_sig,
                url_name=appimage.name,
            )
        ]

    raise SystemExit(f"Unsupported updater target: {target}")


def read_signature(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r", "").replace("\n", "").strip()


def load_manifest(path: Path, version: str, notes: str, *, seed_path: Path | None = None) -> dict:
    source_path = path if path.exists() else seed_path
    if source_path and source_path.exists():
        try:
            manifest = json.loads(source_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    else:
        manifest = {}

    manifest["version"] = version
    manifest["notes"] = notes
    manifest["pub_date"] = manifest.get("pub_date") or ""
    manifest.setdefault("platforms", {})
    return manifest


def copy_artifacts(specs: Iterable[ArtifactSpec], version_root: Path) -> None:
    version_root.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        for rel_path in spec.files:
            src = BUNDLE_ROOT / Path(rel_path)
            if not src.exists():
                raise SystemExit(f"Required artifact not found: {src}")
            shutil.copy2(src, version_root / src.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and collect SciPilot release artifacts.")
    parser.add_argument("--version", default="", help="Release version, defaults to package.json version")
    parser.add_argument("--private-key-path", default=".tauri/updater.key", help="Updater private key path")
    parser.add_argument("--output-dir", default="release", help="Output release directory")
    parser.add_argument("--repo", default="way9999/scipilot", help="GitHub repo used to build latest.json URLs")
    parser.add_argument("--notes-file", default="", help="Optional release notes file")
    parser.add_argument("--seed-manifest", default="", help="Optional existing latest.json used to merge platform entries")
    parser.add_argument("--skip-build", action="store_true", help="Reuse existing bundle artifacts")
    args = parser.parse_args()

    version = args.version.strip() or load_package_version()
    plain_version = version[1:] if version.startswith("v") else version
    tag = version if version.startswith("v") else f"v{version}"
    target = current_target()

    if not args.skip_build:
        key_path = (PROJECT_ROOT / args.private_key_path).resolve()
        if not key_path.exists():
            raise SystemExit(f"Updater private key not found: {key_path}")
        env = os.environ.copy()
        env["TAURI_SIGNING_PRIVATE_KEY"] = str(key_path)
        cmd = ["pnpm", "tauri", "build"]
        if target.startswith("linux-"):
            cmd.extend(["--bundles", "appimage"])
        run(cmd, env=env)

    notes = f"SciPilot {plain_version}"
    if args.notes_file:
        notes = Path(args.notes_file).read_text(encoding="utf-8").strip()

    specs = discover_artifacts(target, plain_version)
    release_root = PROJECT_ROOT / args.output_dir / plain_version
    copy_artifacts(specs, release_root)

    manifest_path = release_root / "latest.json"
    seed_manifest = Path(args.seed_manifest) if args.seed_manifest else None
    manifest = load_manifest(manifest_path, plain_version, notes, seed_path=seed_manifest)
    manifest["pub_date"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    release_base_url = f"https://github.com/{args.repo}/releases/download/{tag}"

    for spec in specs:
        sig_path = BUNDLE_ROOT / Path(spec.signature)
        manifest["platforms"][spec.key] = {
            "signature": read_signature(sig_path),
            "url": f"{release_base_url}/{spec.url_name}",
        }

    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Release artifacts copied to {release_root}")
    for item in sorted(release_root.iterdir()):
        print(f"- {item.name}")


if __name__ == "__main__":
    main()
