ARG PYTHON_BASE=python@sha256:32432ed74044491491c7bd9e11f78d25f7053218ca39af3d5bb038280610086b
FROM ${PYTHON_BASE}
ARG UV_VERSION=0.13.5
ENV PYTHONUNBUFFERED=1 LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC \
    UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PATH="/app/.venv/bin:/usr/local/bin:${PATH}"
WORKDIR /app
RUN pip install --no-cache-dir uv==${UV_VERSION}
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --all-groups
COPY config/pilot.toml config/environments.json ./config/
COPY tests ./tests
COPY scripts ./scripts
COPY docker ./docker
COPY README.md ./README.md
# The generated lock is mounted read-only by Compose: no self-referential image hash.
CMD ["python", "--version"]
