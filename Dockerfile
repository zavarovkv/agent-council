FROM node:24-alpine@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS web-assets
WORKDIR /styles
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY assets/login/telegram-widget.js assets/login/telegram-widget.js
RUN npm run check

# Reviewed official delegation plugin, pinned by commit and archive checksum.
ADD --checksum=sha256:9556bcf79667da701b98a88be512b0f98c90c8445517b9b2aebbd96e21751443 https://codeload.github.com/NousResearch/hermes-official-plugins/tar.gz/e404b6816b20237a82f6fbe2749b5a576cf75dba /tmp/hermes-plugins.tar.gz
RUN mkdir -p /vendor \
    && tar -xzf /tmp/hermes-plugins.tar.gz -C /vendor --strip-components=1 \
       hermes-official-plugins-e404b6816b20237a82f6fbe2749b5a576cf75dba/advanced-delegation

FROM nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d

LABEL org.opencontainers.image.source="https://github.com/zavarovkv/agent-council"
LABEL org.opencontainers.image.description="Hermes integration for Agent Council"

COPY --from=web-assets /vendor/advanced-delegation/ /opt/hermes/plugins/advanced-delegation/
COPY agents/ /opt/agent-council/agents/
COPY scripts/provision-researcher.py /opt/agent-council/provision-researcher.py
RUN chmod -R a+rX,go-w /opt/hermes/plugins/advanced-delegation /opt/agent-council/agents \
    && chmod 0444 /opt/agent-council/provision-researcher.py

COPY scripts/initialize-profile.py /opt/agent-council/initialize-profile.py
COPY scripts/initialize-profile.sh /etc/cont-init.d/03-council-session-store
RUN chmod 0555 /etc/cont-init.d/03-council-session-store \
    && chmod 0444 /opt/agent-council/initialize-profile.py

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
