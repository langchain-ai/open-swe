import asyncio
import json
from collections import Counter
from pathlib import Path

from PIL import Image


class Branding:
    ROOT = Path(__file__).resolve().parents[1]
    PUBLIC = ROOT / "ui" / "public"
    SOURCE = PUBLIC / "logo-mark.png"

    LOGO_BACKGROUND = (127, 200, 255)

    @classmethod
    async def update_apple(cls, color: tuple[int, int, int]) -> None:
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in (channel / 255 for channel in color)
        ]
        matrix = (
            (0.82259287, 0.17753395, 0.0),
            (0.03319951, 0.96678350, 0.0),
            (0.01708535, 0.07239572, 0.91030148),
        )
        channels = [sum(a * b for a, b in zip(row, linear, strict=True)) for row in matrix]
        encoded = [
            12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
            for value in channels
        ]
        path = cls.ROOT / "mobile/assets/icon.icon/icon.json"
        asset = json.loads(path.read_text())
        asset["fill"]["solid"] = (
            "display-p3:" + ",".join(f"{value:.5f}" for value in encoded) + ",1.00000"
        )
        path.write_text(json.dumps(asset, indent=2, separators=(",", " : ")) + "\n")

    @classmethod
    async def generate(cls) -> None:
        color = cls.LOGO_BACKGROUND
        with Image.open(cls.SOURCE) as source:
            icon = source.convert("RGBA")
        background = Counter(
            pixel[:3] for pixel in icon.get_flattened_data() if pixel[3] == 255
        ).most_common(1)[0][0]
        icon.putdata(
            [
                (*color, alpha) if (red, green, blue) == background else (red, green, blue, alpha)
                for red, green, blue, alpha in icon.get_flattened_data()
            ]
        )
        if background != color:
            icon.save(cls.SOURCE)
        await cls.update_apple(color)
        for path, size in (
            (cls.PUBLIC / "favicon.png", 192),
            (cls.PUBLIC / "apple-touch-icon.png", 180),
            (cls.PUBLIC / "logo512.png", 512),
            (cls.ROOT / "ui/assets/ic_launcher_round.png", 192),
            (cls.ROOT / "ui/assets/playstore-icon.png", 512),
        ):
            icon.resize((size, size), Image.Resampling.LANCZOS).save(path)
        bird = icon.crop((180, 230, 850, 800))
        bird.putalpha(
            Image.eval(bird.getchannel("B"), lambda blue: max(0, (255 - blue) * 255 / 239))
        )
        bird = bird.crop(bird.getbbox())
        bird.save(cls.PUBLIC / "bird-mark.png")
        for environment, color in (
            ("preview", (255, 107, 157)),
            ("staging", (92, 225, 166)),
        ):
            colored_bird = Image.new("RGBA", bird.size, color)
            colored_bird.putalpha(bird.getchannel("A"))
            colored_bird.save(cls.PUBLIC / f"bird-mark-{environment}.png")
            variant = icon.copy()
            variant.putdata(
                [
                    (*color, alpha) if blue > red + 40 else (red, green, blue, alpha)
                    for red, green, blue, alpha in icon.get_flattened_data()
                ]
            )
            variant.save(cls.PUBLIC / f"logo-mark-{environment}.png")
            variant.resize((192, 192), Image.Resampling.LANCZOS).save(
                cls.PUBLIC / f"favicon-{environment}.png"
            )


if __name__ == "__main__":
    asyncio.run(Branding.generate())
