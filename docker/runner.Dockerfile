ARG NODE_BASE
FROM ${NODE_BASE}
ARG NPM_VERSION=10.8.2
ARG PM_PACKAGE=npm
ARG PM_VERSION=10.8.2
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC PATH="/opt/pm/bin:${PATH}"
WORKDIR /app
# Do not rely on bundled npm/Corepack or the bundled Yarn Classic links.
RUN rm -f /usr/local/bin/yarn /usr/local/bin/yarnpkg /usr/local/bin/corepack \
    && npm install --global npm@${NPM_VERSION} \
    && if [ "$PM_PACKAGE" != npm ]; then \
         npm install --global --prefix /opt/pm "${PM_PACKAGE}@${PM_VERSION}"; \
       fi \
    && npm cache clean --force
CMD ["node", "--version"]
