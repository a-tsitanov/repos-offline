# Песочница: node + npm + uv + Python 3.12. Сеть — только через прокси стека.
FROM ghcr.io/astral-sh/uv:0.11.18 AS uv

FROM node:24-bookworm-slim
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install 3.12 \
 && chmod -R a+rX /opt/uv-python \
 && mkdir -p /tmp/offpack \
 && chown node:node /tmp/offpack
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_NO_PROGRESS=1 \
    npm_config_update_notifier=false \
    npm_config_fund=false \
    npm_config_audit=false
USER node
WORKDIR /tmp/offpack
