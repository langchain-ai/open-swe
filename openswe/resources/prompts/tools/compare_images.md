Compare two screenshots of the same size, pixel by pixel, and save images of what changed.

Take both at one viewport size; full pages of different heights cannot be compared.

Args:
- before: Absolute path under `/workspace/` of the earlier image.
- after: Absolute path under `/workspace/` of the later image.
- diff: Where to save the later image with the changed pixels highlighted; defaults to `<after>-diff.png`.
- mask: Where to save the changed pixels alone, on transparency; defaults to `<after>-mask.png`.
- threshold: How much a pixel's largest RGBA channel must change to count, from 0 to 255; default 0.

Returns: `{success: True, width, height, metrics: {changed_pixels, total_pixels, changed_ratio, threshold}, regions, diff, mask}`, with the largest changed regions first, or `{success: False, error}`.
