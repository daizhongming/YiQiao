FROM python:3.12-slim AS builder

WORKDIR /src

ARG PIP_INDEX_URL=https://pypi.org/simple

COPY yiqiao-mcp/pyproject.toml ./
COPY yiqiao-mcp/src ./src

RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG PIP_INDEX_URL=https://pypi.org/simple

RUN addgroup --system --gid 10001 yiqiao \
    && adduser --system --uid 10001 --ingroup yiqiao --no-create-home yiqiao

COPY --from=builder /wheels /tmp/wheels
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md MODIFICATIONS.md /usr/share/licenses/yiqiao/

RUN python -m pip install --no-cache-dir /tmp/wheels/*.whl \
    && python -m pip check \
    && rm -rf /tmp/wheels

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()"

CMD ["yiqiao-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
