"""Image generation module — generate academic figures via AI image models.

Supports multiple backends (OpenAI-compatible image generation APIs).
Generated images avoid text rendering — labels are overlaid via matplotlib
so both Chinese and English work correctly.
"""

from __future__ import annotations

import base64
import json
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import requests


def _load_config() -> dict[str, str] | None:
    """Load image gen config from environment or settings."""
    api_key = os.environ.get("IMAGE_GEN_API_KEY", "").strip()
    base_url = os.environ.get("IMAGE_GEN_BASE_URL", "").strip()
    model = os.environ.get("IMAGE_GEN_MODEL", "").strip()

    if api_key and base_url:
        return {"api_key": api_key, "base_url": base_url, "model": model or "nano-banana-2"}

    tool_root = Path(__file__).resolve().parent.parent
    env_settings_path = os.environ.get("SCIPILOT_SETTINGS_PATH", "").strip()
    settings_candidates = []
    if env_settings_path:
        settings_candidates.append(Path(env_settings_path))
    settings_candidates.extend([
        Path("scipilot/settings.json"),
        Path("settings.json"),
        tool_root / "scipilot" / "settings.json",
        tool_root / "settings.json",
        Path.home() / ".scipilot" / "settings.json",
    ])
    for sp in settings_candidates:
        if not sp.exists():
            continue
        try:
            settings = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            continue
        api_keys = settings.get("api_keys", {}) or {}
        base_urls = settings.get("api_base_urls", {}) or {}
        settings_model = str(settings.get("image_gen_model") or "").strip()
        effective_model = model or settings_model or "nano-banana-2"
        ig_key = api_keys.get("image_gen", "")
        ig_url = base_urls.get("image_gen", "")
        if ig_key and ig_url:
            return {"api_key": ig_key, "base_url": ig_url, "model": effective_model}
        for provider in ("llm", "openai"):
            key = api_keys.get(provider, "")
            url = base_urls.get(provider, "")
            if key and url:
                return {"api_key": key, "base_url": url, "model": effective_model}

    return None


def generate_image(
    prompt: str,
    output_path: str | Path,
    config: dict[str, str] | None = None,
    size: str = "1024x1024",
    style_hint: str = "academic paper diagram, clean, professional, blue and white, no text labels",
) -> Path | None:
    """Generate an image via API and save to output_path.

    The prompt describes the visual content. style_hint ensures academic quality.
    Text labels should NOT be in the generated image — overlay them separately.
    """
    config = config or _load_config()
    if not config:
        print("[ImageGen] No API config available")
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    full_prompt = f"{prompt}. Style: {style_hint}"

    base_url = config["base_url"].rstrip("/")
    # Strip a trailing /v1 so we can cleanly append the specific endpoint path.
    api_root = base_url[:-3] if base_url.endswith("/v1") else base_url

    # Try OpenAI-compatible /v1/images/generations endpoint first.
    try:
        resp = requests.post(
            f"{api_root}/v1/images/generations",
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": config["model"],
                "prompt": full_prompt,
                "n": 1,
                "size": size,
                "response_format": "b64_json",
            },
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            images = data.get("data", [])
            if images:
                b64 = images[0].get("b64_json") or ""
                url = images[0].get("url") or ""
                if b64:
                    output_path.write_bytes(base64.b64decode(b64))
                    print(f"[ImageGen] Generated: {output_path.name}")
                    return output_path
                elif url:
                    img_resp = requests.get(url, timeout=60)
                    if img_resp.status_code == 200:
                        output_path.write_bytes(img_resp.content)
                        print(f"[ImageGen] Generated: {output_path.name}")
                        return output_path
        else:
            # Fall through to chat-completion fallback below.
            print(f"[ImageGen] images/generations returned {resp.status_code}; trying chat fallback")
    except Exception as exc:
        print(f"[ImageGen] images/generations error: {exc}; trying chat fallback")

    # Fallback: some multimodal chat models (e.g. Gemini's nano-banana series)
    # accept an image-generation request on /v1/chat/completions and return the
    # bytes as a base64 data URI inside the assistant message content or as
    # Gemini-style inline data.
    try:
        resp = requests.post(
            f"{api_root}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": config["model"],
                "messages": [{"role": "user", "content": full_prompt}],
                "modalities": ["text", "image"],
                "max_tokens": 1024,
            },
            timeout=180,
        )
        if resp.status_code != 200:
            print(f"[ImageGen] chat-fallback HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        payload = resp.json()
        # Shape 1 (OpenAI-compatible): choices[0].message.content is a string
        # containing a markdown data-URI image link.
        b64_data: str | None = None
        for choice in payload.get("choices", []) or []:
            msg = choice.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                m = re.search(r'data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=]+)', content)
                if m:
                    b64_data = m.group(1)
                    break
            # Shape 2: content is a list of content parts.
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        url = (part.get("image_url") or {}).get("url") if isinstance(part.get("image_url"), dict) else part.get("image_url")
                        if isinstance(url, str):
                            m = re.search(r'data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=]+)', url)
                            if m:
                                b64_data = m.group(1)
                                break
                if b64_data:
                    break
        # Shape 3: Gemini-native `candidates[0].content.parts[*].inlineData.data`
        if not b64_data:
            for cand in payload.get("candidates", []) or []:
                for part in ((cand.get("content") or {}).get("parts") or []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if isinstance(inline, dict) and inline.get("data"):
                        b64_data = inline["data"]
                        break
                if b64_data:
                    break
        if not b64_data:
            print("[ImageGen] chat-fallback returned no image payload")
            return None
        output_path.write_bytes(base64.b64decode(b64_data))
        print(f"[ImageGen] Generated (chat fallback): {output_path.name}")
        return output_path
    except Exception as exc:
        print(f"[ImageGen] chat-fallback error: {exc}")

    return None


def overlay_text_labels(
    image_path: str | Path,
    labels: list[dict[str, Any]],
    output_path: str | Path | None = None,
    language: str = "zh",
) -> Path:
    """Overlay text labels on an image using matplotlib.

    Labels format: [{"text": "...", "x": 0.5, "y": 0.9, "fontsize": 12, "color": "white"}]
    x, y are normalized coordinates (0-1) relative to image size.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.image as mpimg
    except ImportError:
        print("[ImageGen] matplotlib not available for text overlay")
        return Path(image_path)

    image_path = Path(image_path)
    output_path = Path(output_path) if output_path else image_path

    img = mpimg.imread(str(image_path))
    fig, ax = plt.subplots(figsize=(img.shape[1] / 100, img.shape[0] / 100), dpi=100)
    ax.imshow(img)
    ax.axis("off")

    # Font selection based on language
    font_family = "SimHei" if language == "zh" else "Times New Roman"
    for label in labels:
        ax.text(
            label.get("x", 0.5),
            label.get("y", 0.5),
            label["text"],
            transform=ax.transAxes,
            fontsize=label.get("fontsize", 12),
            color=label.get("color", "white"),
            fontfamily=label.get("fontfamily", font_family),
            ha=label.get("ha", "center"),
            va=label.get("va", "center"),
            bbox=label.get("bbox"),
        )

    plt.savefig(str(output_path), bbox_inches="tight", pad_inches=0, dpi=150)
    plt.close()
    return output_path


# --- Figure requirement analysis ---

FIGURE_TEMPLATES: dict[str, dict[str, Any]] = {
    "architecture": {
        "type": "ai_gen",
        "prompt": "System architecture diagram showing modular design with clear hierarchy",
        "description_zh": "系统总体架构图",
        "description_en": "System Architecture",
    },
    "flowchart": {
        "type": "ai_gen",
        "prompt": "Algorithm flowchart with decision branches and process steps",
        "description_zh": "算法流程图",
        "description_en": "Algorithm Flowchart",
    },
    "data_flow": {
        "type": "ai_gen",
        "prompt": "Data flow diagram showing how information moves between system modules",
        "description_zh": "数据流图",
        "description_en": "Data Flow Diagram",
    },
    "comparison_bar": {
        "type": "matplotlib",
        "description_zh": "方法对比柱状图",
        "description_en": "Method Comparison Bar Chart",
    },
    "sensitivity_curve": {
        "type": "matplotlib",
        "description_zh": "参数敏感性曲线",
        "description_en": "Parameter Sensitivity Curve",
    },
    "coverage_heatmap": {
        "type": "matplotlib",
        "description_zh": "覆盖率热力图",
        "description_en": "Coverage Heatmap",
    },
}


def analyze_figure_needs(
    sections: list[dict[str, Any]],
    language: str = "zh",
    existing_count: int = 0,
) -> list[dict[str, Any]]:
    """Analyze paper sections and determine what figures are needed.

    Returns a list of figure requirements.
    """
    section_titles = [s.get("title", "") for s in sections]
    needs: list[dict[str, Any]] = []

    for title in section_titles:
        title_lower = title.lower()
        if any(kw in title_lower for kw in ("绪论", "introduction", "1")):
            needs.append({**FIGURE_TEMPLATES["architecture"], "chapter": "1"})
        if any(kw in title_lower for kw in ("原理", "理论", "theory", "principle", "2")):
            needs.append({**FIGURE_TEMPLATES["flowchart"], "chapter": "2"})
            needs.append({**FIGURE_TEMPLATES["data_flow"], "chapter": "2"})
        if any(kw in title_lower for kw in ("设计", "实现", "design", "implement", "3")):
            needs.append({**FIGURE_TEMPLATES["data_flow"], "chapter": "3"})
        if any(kw in title_lower for kw in ("实验", "experiment", "result", "4")):
            needs.append({**FIGURE_TEMPLATES["comparison_bar"], "chapter": "4"})
            needs.append({**FIGURE_TEMPLATES["sensitivity_curve"], "chapter": "4"})

    # Deduplicate by type
    seen_types: set[str] = set()
    unique: list[dict[str, Any]] = []
    for n in needs:
        key = n.get("type", "") + n.get("chapter", "")
        if key not in seen_types:
            seen_types.add(key)
            unique.append(n)

    return unique


def ensure_figures(
    project_root: str | Path,
    sections: list[dict[str, Any]],
    language: str = "zh",
    config: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Ensure sufficient figures exist for the paper.

    1. Scan existing figures in output/figures/
    2. Analyze what's needed based on paper sections
    3. Generate missing figures via AI or matplotlib
    4. Return dict with keys: generated, errors, skipped
    """
    project_root = Path(project_root)
    fig_dir = project_root / "output" / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    existing = list(fig_dir.glob("*.png"))
    needs = analyze_figure_needs(sections, language, existing_count=len(existing))

    generated: list[Path] = []
    errors: list[str] = []
    skipped: list[str] = []

    for need in needs:
        fig_type = need.get("type", "")
        desc = need.get("description_zh" if language == "zh" else "description_en", "Figure")
        chapter = need.get("chapter", "0")
        output_name = f"gen_{chapter}_{fig_type}.png"
        output_file = fig_dir / output_name

        if output_file.exists():
            generated.append(output_file)
            continue

        if fig_type == "ai_gen":
            try:
                path = generate_image(
                    prompt=need["prompt"],
                    output_path=output_file,
                    config=config,
                )
                if path:
                    generated.append(path)
                else:
                    errors.append(f"{output_name}: generate_image returned None")
            except Exception as exc:
                errors.append(f"{output_name}: {exc}")
        elif fig_type == "matplotlib":
            # matplotlib figures need data — generate placeholder for now
            # Actual data-driven generation happens via figure_generator.py
            skipped.append(f"{output_name} (matplotlib type, needs data)")

    return {"generated": generated, "errors": errors, "skipped": skipped}
