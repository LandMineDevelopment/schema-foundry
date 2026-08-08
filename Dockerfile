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
    SCHEMA_FOUNDRY_HOST=0.0.0.0 \
    SCHEMA_FOUNDRY_PORT=8080 \
    SCHEMA_FOUNDRY_CONFIG_DIR=/data/config \
    SCHEMA_FOUNDRY_SCHEMA_DIR=/data/schemas \
    SCHEMA_FOUNDRY_BEHIND_LOOPBACK_PROXY=1

COPY --from=builder /opt/venv /opt/venv

RUN useradd --create-home --uid 10001 schema-foundry \
    && mkdir -p /data/config /data/schemas \
    && chown -R schema-foundry:schema-foundry /data

USER schema-foundry
EXPOSE 8080

CMD ["schema-foundry"]
