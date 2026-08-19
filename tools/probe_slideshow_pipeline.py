"""One-item smoke test for the AI-style-to-slideshow pipeline."""

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env() -> None:
    env_path = ROOT / ".env"
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    load_env()
    import config
    from PIL import Image, ImageDraw
    from ai import TaskRequest
    from ai.providers.image.seedream import SeedreamProvider

    test_dir = ROOT / "work_temp" / "slideshow_e2e"
    test_dir.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR = test_dir
    source = test_dir / "synthetic_source.png"
    canvas = Image.new("RGB", (768, 1024), "#f1c27d")
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((120, 150, 650, 680), fill="#69b7ff", outline="#173f5f", width=18)
    draw.polygon([(384, 100), (680, 850), (90, 850)], fill="#ff6f61", outline="#7b241c")
    draw.rectangle((210, 390, 555, 780), fill="#65c18c", outline="#145a32", width=14)
    canvas.save(source)

    provider = SeedreamProvider(api_key=os.environ.get("SEEDREAM_API_KEY", ""))
    request = TaskRequest(
        operation="image_edit",
        inputs={
            "image": str(source),
            "prompt": "转换为柔和的日系动画插画风格，保留原图主体、构图和姿态，清晰线条，明亮色彩",
        },
        params={"size": "1K", "quality": "standard", "n": 1, "strength": 0.6},
    )
    print(f"SOURCE={source}")
    handle = provider.execute(request)
    if not handle.is_success or handle.result is None:
        error = handle.result.error if handle.result else "unknown error"
        print(f"IMAGE_EDIT_FAILED={error}")
        return 2
    output = Path(handle.result.data)
    print(f"IMAGE_EDIT_OK={output}")
    print(f"IMAGE_BYTES={output.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
