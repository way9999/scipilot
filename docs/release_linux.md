# SciPilot Linux Packaging

## Goal

The first Linux target is `AppImage`.

That gives Linux users a single portable binary:

```bash
chmod +x SciPilot_*.AppImage
./SciPilot_*.AppImage
```

## Prerequisites

Build the Linux package on a Linux machine or Ubuntu CI runner.

Typical Ubuntu dependencies for Tauri 2:

```bash
sudo apt-get update
sudo apt-get install -y \
  libwebkit2gtk-4.1-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  patchelf
```

You also need:

```bash
python3 -m pip install pyinstaller
pnpm install
```

If updater signing is enabled, place the updater private key at:

```text
.tauri/updater.key
```

## Build

Run the cross-platform release script from the `scipilot/` directory:

```bash
python scripts/build_release.py
```

On Linux, the script:

- runs `pnpm tauri build --bundles appimage`
- collects the generated `AppImage` and `.sig`
- writes or merges `release/<version>/latest.json`
- copies artifacts into `release/<version>/`

To reuse existing bundle artifacts:

```bash
python scripts/build_release.py --skip-build
```

## Output

The Linux package will be placed in:

```text
release/<version>/
```

Typical files:

- `SciPilot_<version>_amd64.AppImage`
- `SciPilot_<version>_amd64.AppImage.sig`
- `latest.json`

## Notes

- Linux packaging should be done on Linux, not on Windows.
- The current release script merges platform entries into the same `latest.json`, so Windows and Linux updater entries can coexist.
- The frontend updater fallback now resolves Linux download URLs from the merged manifest.

## GitHub Actions

This repository now includes:

```text
.github/workflows/build-linux-appimage.yml
```

It is designed for `workflow_dispatch` on Ubuntu and will:

- install Linux Tauri build dependencies
- build the AppImage
- download the existing `latest.json` from the matching GitHub Release when present
- merge Linux updater entries into `release/<version>/latest.json`
- upload Linux assets back to the same release

Required repository secrets:

- `TAURI_SIGNING_PRIVATE_KEY`
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

Recommended usage:

1. Keep Windows release assets already published for the target version.
2. Trigger `build-linux-appimage` manually in GitHub Actions.
3. Let the workflow append Linux assets and merged `latest.json` to the same release tag.
