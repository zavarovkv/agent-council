FROM nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d

LABEL org.opencontainers.image.source="https://github.com/zavarovkv/agent-council"
LABEL org.opencontainers.image.description="Hermes integration for Agent Council"

# Ship the owner policy as immutable application code, separate from user data.
COPY src/telegram_auth/ /opt/hermes/plugins/dashboard_auth/telegram_owner/
RUN chmod -R a+rX,go-w /opt/hermes/plugins/dashboard_auth/telegram_owner

COPY assets/login/ /opt/agent-council-theme/
COPY scripts/install-login-theme.py /opt/agent-council-theme/install.py
RUN python3 /opt/agent-council-theme/install.py /opt/hermes/hermes_cli/dashboard_auth/login_page.py /opt/agent-council-theme \
    && chmod -R a+rX,go-w /opt/agent-council-theme
