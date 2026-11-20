FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
COPY evals/ /workspace/evals/
COPY scan.py /usr/local/lib/plain-eval/scan.py
# The HTML, SVG and image files the image ships with; anything new is a run's artifact.
RUN python3 /usr/local/lib/plain-eval/scan.py > /usr/local/lib/plain-eval/before.txt
WORKDIR /workspace
