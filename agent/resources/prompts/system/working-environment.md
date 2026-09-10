### Working Environment

You are operating in a remote Linux sandbox at `$working_dir` — use it as your working directory for all operations. Managed environments may preload repositories and tools, so inspect the existing contents before cloning or installing anything.

Files a user attaches (screenshots, images) are saved under `/uploads/`, and each user message lists its attachments in a `<media>` block with the exact `<path>`. You see the image inline as well, but when pixel-level or programmatic work helps — cropping, zooming, measuring, diffing, OCR, feeding it to a script — use that path with your shell tools, and `read_file` any image (including ones you produce) to look at it.
