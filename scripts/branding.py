import asyncio
from pathlib import Path

from PIL import Image


class Branding:
    ROOT = Path(__file__).resolve().parents[1]
    PUBLIC = ROOT / "ui" / "public"
    SOURCE = PUBLIC / "logo-mark.png"

    @classmethod
    async def generate(cls) -> None:
        with Image.open(cls.SOURCE) as source:
            icon = source.convert("RGBA")
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
