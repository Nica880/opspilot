"""Build docs/img/demo.gif from the Playwright walkthrough frames (docs/img/frames/*.png).

Regenerate frames:  cd web && SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts
Then:               uv run python scripts/make_demo_gif.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / "docs" / "img" / "frames"
OUT = ROOT / "docs" / "img" / "demo.gif"
WIDTH = 960
SECONDS_PER_FRAME = 13

CAPTIONS = {
    "inbox": "Inbox: new issues with live triage status (SSE)",
    "issue": "Issue view: classification, cited draft (verified), evidence",
    "duplicates": "Likely duplicates side by side with the LLM's reasoning",
    "approvals": "Approval queue: nothing is posted without a human",
    "approvals-edit": "Edit labels or reply: edits become ground truth",
    "approvals-sent": "Decision signals the Temporal workflow; sandbox repo only",
    "search": "Search playground: dense vs hybrid vs hybrid + rerank",
    "evals": "Eval dashboard: red-team, retrieval and triage reports",
    "ops": "Ops: workflow states, review rates, Redpanda consumer lag",
}
FOOTER = "UI recorded with a mocked API (fixture data)"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def caption(frame: Image.Image, text: str) -> Image.Image:
    scale = WIDTH / frame.width
    img = frame.convert("RGB").resize((WIDTH, int(frame.height * scale)), Image.Resampling.LANCZOS)
    bar = 56
    canvas = Image.new("RGB", (WIDTH, img.height + bar), (17, 17, 17))
    canvas.paste(img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((16, img.height + 8), text, fill=(255, 255, 255), font=_font(20))
    draw.text((16, img.height + 34), FOOTER, fill=(160, 160, 160), font=_font(13))
    return canvas


def main() -> None:
    paths = sorted(FRAMES.glob("*.png"))
    if not paths:
        raise SystemExit(f"no frames in {FRAMES}")
    frames = []
    for path in paths:
        key = path.stem.split("-", 1)[1]
        frames.append(caption(Image.open(path), CAPTIONS.get(key, key)))
    palette = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f in frames]
    palette[0].save(
        OUT,
        save_all=True,
        append_images=palette[1:],
        duration=SECONDS_PER_FRAME * 1000,
        loop=0,
        optimize=True,
    )
    total = SECONDS_PER_FRAME * len(frames)
    print(f"wrote {OUT} ({len(frames)} frames, {total} s, {OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
