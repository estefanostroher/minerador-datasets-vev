# Recipe to finalize and build in T08, once src/server.ts and tests exist.
ARG NODE_BASE=node@sha256:16d364eebf6b62da439dc993d9b80940c78b0ca38438452f011ab9a25c752644
FROM ${NODE_BASE}
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
WORKDIR /app
RUN npm install --global npm@10.8.2
COPY tools/ts-analyzer/package.json tools/ts-analyzer/package-lock.json ./
RUN npm ci
COPY tools/ts-analyzer/tsconfig.json ./
COPY tools/ts-analyzer/src ./src
COPY tools/ts-analyzer/test ./test
RUN npm run build
CMD ["node", "dist/src/server.js"]
