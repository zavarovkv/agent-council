FROM node:24-alpine@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS web-assets
WORKDIR /styles
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY assets/login/telegram-widget.js assets/login/telegram-widget.js
RUN npm run check

FROM nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d

LABEL org.opencontainers.image.source="https://github.com/zavarovkv/agent-council"
LABEL org.opencontainers.image.description="Hermes integration for Agent Council"

# Ship the owner policy as immutable application code, separate from user data.
COPY src/telegram_auth/ /opt/hermes/plugins/dashboard_auth/telegram_owner/
RUN chmod -R a+rX,go-w /opt/hermes/plugins/dashboard_auth/telegram_owner

COPY assets/login/ /opt/agent-council-theme/
COPY --from=web-assets /styles/node_modules/beauty-css/dist/beauty.min.css /opt/agent-council-theme/dependencies/beauty.min.css
COPY --from=web-assets /styles/node_modules/beauty-css/LICENSE /opt/agent-council-theme/dependencies/beauty-css.LICENSE
COPY --from=web-assets /styles/node_modules/beauty-css/package.json /opt/agent-council-theme/dependencies/beauty-package.json
COPY scripts/install-login-theme.py /opt/agent-council-theme/install.py
RUN python3 /opt/agent-council-theme/install.py /opt/hermes/hermes_cli/dashboard_auth/login_page.py /opt/agent-council-theme \
    && chmod -R a+rX,go-w /opt/agent-council-theme
