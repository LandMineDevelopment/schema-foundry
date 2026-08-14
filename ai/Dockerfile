FROM node:22.22.2-alpine3.22 AS tool-dependencies

WORKDIR /dependencies
COPY workspace/.opencode/package*.json ./
RUN npm ci --omit=dev --omit=optional --ignore-scripts

FROM ghcr.io/anomalyco/opencode:1.18.15

COPY --from=tool-dependencies /dependencies/node_modules /opt/opencode/node_modules
COPY --chmod=0555 secret-entrypoint.sh /usr/local/bin/schemii-opencode
ENTRYPOINT ["/usr/local/bin/schemii-opencode"]
