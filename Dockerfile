FROM langchain/langgraph-api:0.15.1-py3.14

ADD . /deps/open-swe
RUN cd /deps/open-swe \
    && PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir -c /api/constraints.txt -e .

ENV LANGGRAPH_HTTP='{"app":"openswe.webapp:app"}'
ENV LANGGRAPH_CHECKPOINTER='{"ttl":{"strategy":"delete","sweep_interval_minutes":60,"default_ttl":43200}}'
ENV LANGSERVE_GRAPHS='{"agent":"openswe.graphs.agent:traced_agent","reviewer":"openswe.graphs.reviewer:traced_reviewer_agent","analyzer":"openswe.graphs.analyzer:traced_analyzer","review-scout":"openswe.graphs.review_scout:traced_review_scout","chat":"openswe.graphs.chat:traced_chat_agent","scheduler":"openswe.graphs.scheduler:get_scheduler"}'

RUN mkdir -p /api/langgraph_api /api/langgraph_runtime /api/langgraph_license \
    && touch /api/langgraph_api/__init__.py /api/langgraph_runtime/__init__.py /api/langgraph_license/__init__.py
RUN PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir --no-deps -e /api
RUN pip uninstall -y pip setuptools wheel
RUN rm -rf /usr/local/lib/python*/site-packages/pip* /usr/local/lib/python*/site-packages/setuptools* /usr/local/lib/python*/site-packages/wheel* \
    && find /usr/local/bin -name "pip*" -delete || true
RUN rm -rf /usr/lib/python*/site-packages/pip* /usr/lib/python*/site-packages/setuptools* /usr/lib/python*/site-packages/wheel* \
    && find /usr/bin -name "pip*" -delete || true
RUN uv pip uninstall --system pip setuptools wheel \
    && rm /usr/bin/uv /usr/bin/uvx

WORKDIR /deps/open-swe

EXPOSE 8000
