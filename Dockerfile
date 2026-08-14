FROM python:3.12-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY pyproject.toml ./
COPY README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCHEMII_HOST=0.0.0.0 \
    SCHEMII_PORT=8080 \
    SCHEMII_CONFIG_DIR=/data/config \
    SCHEMII_SCHEMA_DIR=/data/schemas \
    SCHEMII_BEHIND_LOOPBACK_PROXY=1

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0555 docker/runtime-secret-entrypoint.sh /usr/local/bin/schemii-runtime

RUN useradd --create-home --uid 10001 schemii \
    && mkdir -p /data/config /data/schemas /data/dashboards \
    && chown -R schemii:schemii /data

USER root
ENTRYPOINT ["/usr/local/bin/schemii-runtime"]
EXPOSE 8080

FROM runtime AS schemer-runtime
ENV SCHEMER_HOST=0.0.0.0 \
    SCHEMER_PORT=8081 \
    SCHEMER_CONFIG_DIR=/data/config \
    SCHEMER_DASHBOARD_DIR=/data/dashboards \
    SCHEMER_BEHIND_LOOPBACK_PROXY=1
EXPOSE 8081
CMD ["schemer"]

FROM runtime AS schemii-runtime
CMD ["schemii"]
