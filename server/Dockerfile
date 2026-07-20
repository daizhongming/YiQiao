# This file was modified in 2026 by YiQiao contributors. See NOTICE.

FROM python:3.12-slim AS package-builder

WORKDIR /src

ARG PIP_INDEX_URL=https://pypi.org/simple

COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md MODIFICATIONS.md ./
COPY mem0 ./mem0

RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

ARG DEBIAN_MIRROR=https://deb.debian.org
ARG PIP_INDEX_URL=https://pypi.org/simple

RUN sed -i \
        -e "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" \
        -e "s|https://deb.debian.org|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install --yes --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=package-builder /wheels /tmp/wheels
COPY server/requirements.txt /tmp/server-requirements.txt
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md MODIFICATIONS.md /usr/share/licenses/yiqiao/

RUN test -s /usr/share/licenses/yiqiao/LICENSE \
    && test -s /usr/share/licenses/yiqiao/NOTICE \
    && test -s /usr/share/licenses/yiqiao/THIRD_PARTY_NOTICES.md \
    && test -s /usr/share/licenses/yiqiao/MODIFICATIONS.md

# Install the wheel built from this checkout before the server dependencies so
# the image never substitutes an unrelated package from a public index.
RUN python -m pip install --no-cache-dir /tmp/wheels/*.whl \
    && python -m pip install --no-cache-dir -r /tmp/server-requirements.txt \
    && python -m pip check \
    && rm -rf /tmp/wheels /tmp/server-requirements.txt

COPY server /app

RUN mkdir -p /app/history

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
