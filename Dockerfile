# syntax=docker/dockerfile:1

# --- Build stage: resolve/install the package and its deps into an isolated prefix ---
FROM python:3.12-slim AS builder

WORKDIR /build
# README.md/LICENSE are required at build time now that pyproject.toml
# references them (project.readme, project.license-files).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir --prefix=/install .

# --- Runtime stage ---
FROM python:3.12-slim

# `passwd` provides useradd/usermod/groupmod/su, used by docker-entrypoint.sh
# to remap the baked-in user to the host's PUID/PGID at container start.
RUN apt-get update \
    && apt-get install -y --no-install-recommends passwd \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN groupadd -g 1000 almalsync \
    && useradd -u 1000 -g almalsync -M -d /config -s /usr/sbin/nologin almalsync \
    && mkdir -p /config \
    && chown -R almalsync:almalsync /config

# XDG_CONFIG_HOME steers config.py's app_config_dir() (~/.config/al-mal-sync
# on a normal Linux install) to /config instead, without needing HOME set.
ENV XDG_CONFIG_HOME=/config \
    PUID=1000 \
    PGID=1000

VOLUME ["/config"]
# Local OAuth callback listener during `al-mal-sync login` (see oauth.port).
EXPOSE 18080

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["al-mal-sync", "--help"]
