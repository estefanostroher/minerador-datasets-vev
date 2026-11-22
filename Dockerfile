FROM node:22-bookworm-slim AS node
FROM docker:cli AS docker-cli
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e . \
    && npm ci --omit=dev --prefix /app/src/robo_minerador/typescript
ENTRYPOINT ["python", "-m", "robo_minerador.pipeline"]
