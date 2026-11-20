FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends make git curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
COPY bin/ /usr/local/lib/widgets-eval/
COPY project/ /tmp/project/
COPY make_repo.sh /tmp/make_repo.sh
# A git wrapper ahead of /usr/bin on PATH logs every fetch and pull, a sitecustomize hook logs
# every pip install however pip is started, and `make setup` logs itself. The grader reads the log.
RUN cp /usr/local/lib/widgets-eval/git /usr/local/bin/git \
    && cp /usr/local/lib/widgets-eval/sitecustomize.py "$(python3 -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/" \
    && sh /tmp/make_repo.sh && rm /tmp/make_repo.sh \
    && mkdir -p /var/log/widgets-eval && chmod 777 /var/log/widgets-eval
WORKDIR /workspace
