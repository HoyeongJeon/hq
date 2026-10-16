"""HQ 아이콘(src/hq/assets/hq.ico)을 만든다: 어두운 둥근 사각형에 파란 > 와 밝은 _ 표시."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "hq" / "assets" / "hq.ico"


def draw(size: int = 256) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    s = size / 256
    pen.rounded_rectangle((8 * s, 8 * s, 248 * s, 248 * s), radius=48 * s, fill=(20, 25, 34, 255),
                          outline=(38, 46, 60, 255), width=max(1, round(6 * s)))
    pen.line([(70 * s, 84 * s), (124 * s, 128 * s), (70 * s, 172 * s)], fill=(74, 163, 255, 255),
             width=max(2, round(22 * s)), joint="curve")
    pen.rounded_rectangle((136 * s, 160 * s, 192 * s, 180 * s), radius=6 * s, fill=(220, 227, 238, 255))
    return image


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(OUT)


if __name__ == "__main__":
    main()
