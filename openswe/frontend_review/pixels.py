"""Exact changed-pixel evidence, kept separate from design judgments."""

from collections import deque
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageChops

from .schemas import ChangedRegion, DifferenceMetrics


@dataclass(frozen=True)
class PixelComparison:
    mask: bytes
    difference: bytes
    metrics: DifferenceMetrics
    regions: list[ChangedRegion]
    width: int
    height: int


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def compare(before: bytes, after: bytes, *, threshold: int = 18) -> PixelComparison:
    """Highlight only pixels whose largest RGBA channel difference exceeds threshold."""
    if not 0 <= threshold <= 255:
        raise ValueError("Pixel threshold must be between 0 and 255")
    with Image.open(BytesIO(before)) as original, Image.open(BytesIO(after)) as updated:
        if original.size != updated.size:
            raise ValueError("Paired screenshots must have the same dimensions")
        if original.width > 3840 or original.height > 2160:
            raise ValueError("Paired screenshots exceed the supported viewport")
        old = original.convert("RGBA")
        new = updated.convert("RGBA")
    difference = ImageChops.difference(old, new)
    red, green, blue, alpha = difference.split()
    magnitude = ImageChops.lighter(ImageChops.lighter(red, green), ImageChops.lighter(blue, alpha))
    changed = magnitude.point([255 if value > threshold else 0 for value in range(256)])
    count = changed.histogram()[255]
    mask = Image.new("RGBA", old.size, (226, 61, 117, 0))
    mask.putalpha(changed)
    return PixelComparison(
        mask=_png(mask),
        difference=_png(Image.alpha_composite(new.convert("RGBA"), mask)),
        metrics=DifferenceMetrics(
            changed_pixels=count,
            total_pixels=old.width * old.height,
            changed_ratio=count / (old.width * old.height),
            threshold=threshold,
        ),
        regions=_regions(changed),
        width=old.width,
        height=old.height,
    )


def _regions(changed: Image.Image, *, tile_size: int = 32) -> list[ChangedRegion]:
    tiles: dict[tuple[int, int], tuple[int, int, int, int, int]] = {}
    for y in range(0, changed.height, tile_size):
        for x in range(0, changed.width, tile_size):
            tile = changed.crop(
                (x, y, min(x + tile_size, changed.width), min(y + tile_size, changed.height))
            )
            bounds = tile.getbbox()
            if bounds:
                left, top, right, bottom = bounds
                tiles[(x // tile_size, y // tile_size)] = (
                    tile.histogram()[255],
                    x + left,
                    y + top,
                    x + right,
                    y + bottom,
                )
    regions: list[ChangedRegion] = []
    while tiles:
        start = next(iter(tiles))
        queue = deque([start])
        count = 0
        bounds: list[tuple[int, int, int, int]] = []
        while queue:
            point = queue.popleft()
            if point not in tiles:
                continue
            pixels, left, top, right, bottom = tiles.pop(point)
            count += pixels
            bounds.append((left, top, right, bottom))
            x, y = point
            queue.extend((x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)
        if count < 4:
            continue
        left = min(left for left, _, _, _ in bounds)
        top = min(top for _, top, _, _ in bounds)
        right = max(right for _, _, right, _ in bounds)
        bottom = max(bottom for _, _, _, bottom in bounds)
        regions.append(
            ChangedRegion(x=left, y=top, width=right - left, height=bottom - top, pixels=count)
        )
    return sorted(regions, key=lambda region: (-region.pixels, region.y, region.x))[:100]
