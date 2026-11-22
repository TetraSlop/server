# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/tmserver
WORKDIR /src
# Dependencies first, against a placeholder package, so a source change reuses this layer.
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p src/tmserver && touch src/tmserver/__init__.py \
    && /opt/tmserver/bin/pip install ".[postgres,admin]" \
    && /opt/tmserver/bin/pip uninstall --yes tmserver
COPY src ./src
RUN /opt/tmserver/bin/pip install --no-deps . && /opt/tmserver/bin/pip uninstall --yes pip

FROM python:3.13-slim
RUN useradd --system --uid 10001 --create-home --home-dir /var/lib/tmserver tmserver
COPY --from=build /opt/tmserver /opt/tmserver
ENV PATH=/opt/tmserver/bin:$PATH PYTHONUNBUFFERED=1
USER tmserver
WORKDIR /var/lib/tmserver
# Game, profile, and the admin panel when started with --admin-port 8080 --admin-bind 0.0.0.0.
EXPOSE 51241 51220 8080
# Healthy once the game port (51241, 0xC829) is listening; reads /proc so no session is opened.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys; sys.exit(':C829 00000000:0000 0A' not in open('/proc/net/tcp').read())"]
# Bring the schema up to date, then replace the shell with the server. The upgrade
# refuses to run while another server holds the database's writer lock.
# SIGINT stops the server through its KeyboardInterrupt path; PID 1 ignores a default SIGTERM.
STOPSIGNAL SIGINT
ENTRYPOINT ["sh", "-c", "tmserver-db upgrade && exec tmserver --bind 0.0.0.0 \"$@\"", "tmserver"]
