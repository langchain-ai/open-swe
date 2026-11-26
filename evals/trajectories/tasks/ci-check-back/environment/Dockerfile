FROM python:3.12-slim
# A seven-minute CI run that no process of Robin's owns, and a `sleep` that logs each call, so
# the grader can tell a run that came back on a wakeup from one that waited in the shell or
# promised to check back and never did.
COPY bin/ci bin/sleep /usr/local/bin/
RUN mkdir -p /var/lib/ci /var/log/ci-eval \
    && chmod 777 /var/lib/ci /var/log/ci-eval
WORKDIR /workspace
