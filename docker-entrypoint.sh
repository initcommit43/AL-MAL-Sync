#!/bin/sh
# Adjusts the baked-in `almalsync` user/group to match PUID/PGID (so files
# written into the bind-mounted /config volume are owned by the host user
# that ran `docker run`, not a container-only uid), then drops root and
# execs the real command. No-op if the container isn't started as root.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    if [ "$(id -g almalsync)" != "$PGID" ]; then
        groupmod -o -g "$PGID" almalsync
    fi
    if [ "$(id -u almalsync)" != "$PUID" ]; then
        usermod -o -u "$PUID" almalsync
    fi
    chown -R almalsync:almalsync /config

    exec su -s /bin/sh -c 'exec "$0" "$@"' almalsync -- "$@"
fi

exec "$@"
