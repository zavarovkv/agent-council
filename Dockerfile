FROM node:24-alpine@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS web-assets
WORKDIR /styles
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY assets/login/telegram-widget.js assets/login/telegram-widget.js
COPY assets/login/team.js assets/login/team.js
COPY assets/login/preferences.js assets/login/preferences.js
RUN npm run check

# Reviewed official delegation plugin, pinned by commit and archive checksum.
ADD --checksum=sha256:9556bcf79667da701b98a88be512b0f98c90c8445517b9b2aebbd96e21751443 https://codeload.github.com/NousResearch/hermes-official-plugins/tar.gz/e404b6816b20237a82f6fbe2749b5a576cf75dba /tmp/hermes-plugins.tar.gz
RUN mkdir -p /vendor \
    && tar -xzf /tmp/hermes-plugins.tar.gz -C /vendor --strip-components=1 \
       hermes-official-plugins-e404b6816b20237a82f6fbe2749b5a576cf75dba/advanced-delegation

FROM nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d AS telegram-reader
COPY requirements-telegram-reader-build.txt requirements-telegram-reader.txt /tmp/
RUN /opt/hermes/.venv/bin/python -m venv /opt/telegram-reader \
    && /opt/telegram-reader/bin/python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements-telegram-reader-build.txt \
    && /opt/telegram-reader/bin/python -m pip install --no-cache-dir --require-hashes --no-build-isolation -r /tmp/requirements-telegram-reader.txt \
    && /opt/telegram-reader/bin/python -m pip uninstall --yes setuptools wheel packaging pip

FROM telegram-reader AS document-reader
COPY requirements-document-reader.txt /tmp/
RUN /opt/hermes/.venv/bin/python -m venv /tmp/document-build \
    && /tmp/document-build/bin/python -m pip install --no-cache-dir --require-hashes --only-binary=:all: --no-deps --target /opt/document-libs -r /tmp/requirements-document-reader.txt

FROM nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d AS voice-builder
# CPU-only upstream CLI; no Python dependency changes or runtime downloads.
ADD --checksum=sha256:ff1a9053feb509ff9d7729703355541ae9690073a6b1c40eb692c962e0dc1720 https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/v1.9.5 /tmp/whisper.tar.gz
RUN mkdir /whisper && tar -xzf /tmp/whisper.tar.gz -C /whisper --strip-components=1 \
    && cmake -S /whisper -B /whisper/build -DCMAKE_BUILD_TYPE=Release \
       -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF \
       -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF \
    && cmake --build /whisper/build --target whisper-cli --parallel 2 \
    && strip /whisper/build/bin/whisper-cli
ADD --checksum=sha256:ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-small-q5_1.bin /voice-models/small-q5_1.bin
ADD --checksum=sha256:2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987 https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/ggml-silero-v6.2.0.bin /voice-models/silero-v6.2.0.bin

FROM nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d
COPY --from=document-reader /opt/document-libs/ /opt/document-libs/
COPY --from=voice-builder /whisper/build/bin/whisper-cli /opt/voice/whisper-cli
COPY --from=voice-builder /whisper/LICENSE /opt/voice/LICENSE
COPY --from=voice-builder /voice-models/ /opt/voice/models/
RUN chmod 0555 /opt/voice/whisper-cli && chmod 0444 /opt/voice/LICENSE /opt/voice/models/*

ARG COUNCIL_REVISION=development
LABEL org.opencontainers.image.source="https://github.com/zavarovkv/agent-council"
LABEL org.opencontainers.image.description="Hermes integration for Agent Council"
LABEL org.opencontainers.image.revision="${COUNCIL_REVISION}"
LABEL org.opencontainers.image.licenses="MIT"
LABEL io.agent-council.capabilities="private-instructions-v1,instruction-bundles-v2,instruction-composition-v1,research-filing-v1,telegram-guests-v1,telegram-guest-owner-v1,telegram-guest-agent-v1,telegram-guest-context-v1,linear-project-collaboration-v1,linear-owner-review-v1,linear-coordinator-acceptance-v1,workspace-access-v1,recurring-tasks-v1,recurring-no-updates-v1,native-task-routing-v1,material-intake-v1,workspace-index-v1,google-services-v1,linear-agents-v1,agent-specialists-v1,linear-task-management-v1,telegram-reader-v1,telegram-evidence-v1,agent-editor-v1,linear-handoff-v1"

COPY assets/agents/*.png /opt/agent-council/assets/agents/
COPY LICENSE /opt/agent-council/LICENSE
COPY scripts/healthcheck.py scripts/verify-profile.py /opt/agent-council/
RUN chmod 0444 /opt/agent-council/LICENSE /opt/agent-council/healthcheck.py /opt/agent-council/verify-profile.py

COPY --from=web-assets /vendor/advanced-delegation/ /opt/hermes/plugins/advanced-delegation/
COPY instructions/ /opt/agent-council/instructions/
COPY scripts/provision-researcher.py scripts/provision-research-workflow.py /opt/agent-council/
COPY scripts/configure-drive.py /opt/agent-council/configure-drive.py
COPY scripts/provision-workspace-index.py /opt/agent-council/provision-workspace-index.py
COPY scripts/provision-workspace.py /opt/agent-council/provision-workspace.py
COPY scripts/provision-recurring.py /opt/agent-council/provision-recurring.py
COPY scripts/provision-native-tasks.py /opt/agent-council/provision-native-tasks.py
RUN chmod -R a+rX,go-w /opt/hermes/plugins/advanced-delegation /opt/agent-council/instructions \
    && chmod 0444 /opt/agent-council/provision-researcher.py /opt/agent-council/provision-research-workflow.py /opt/agent-council/configure-drive.py \
       /opt/agent-council/provision-workspace-index.py /opt/agent-council/provision-workspace.py \
       /opt/agent-council/provision-recurring.py /opt/agent-council/provision-native-tasks.py

COPY scripts/initialize-profile.py /opt/agent-council/initialize-profile.py
COPY scripts/initialize-profile.sh /etc/cont-init.d/03-council-session-store
RUN chmod 0555 /etc/cont-init.d/03-council-session-store \
    && chmod 0444 /opt/agent-council/initialize-profile.py

# Ship the owner policy as immutable application code, separate from user data.
COPY src/telegram_auth/ /opt/hermes/plugins/dashboard_auth/telegram_owner/
RUN chmod -R a+rX,go-w /opt/hermes/plugins/dashboard_auth/telegram_owner

# Native Telegram SDK hook and a separate tool-free HTTP worker.
# Owner session-surface compatibility is installed explicitly below.
COPY src/telegram_secretary/ /opt/hermes/plugins/council-secretary/
COPY src/secretary_worker/ /opt/agent-council/secretary_worker/
COPY src/linear_agent/ /opt/agent-council/linear_agent/
COPY src/council_team/ /opt/agent-council/council_team/
COPY src/council_team/ /opt/hermes/council_team/
COPY src/council_workspace/ /opt/hermes/council_workspace/
COPY src/council_skills/ /opt/hermes/council_skills/
COPY src/council_owner/ /opt/hermes/council_owner/
COPY src/council_recurring/ /opt/hermes/council_recurring/
COPY src/council_tasks/ /opt/hermes/council_tasks/
COPY src/council_recurring/ /opt/agent-council/council_recurring/
RUN ln -s /opt/hermes/.venv/bin/hermes /usr/local/bin/hermes
COPY scripts/install-owner-context.py /opt/agent-council/install-owner-context.py
RUN python3 /opt/agent-council/install-owner-context.py /opt/hermes
COPY src/research_filing/ /opt/hermes/research_filing/
COPY src/workspace_plugin/ /opt/hermes/plugins/council-workspace/
COPY src/telegram_monitor/ /opt/agent-council/telegram_monitor/
COPY --from=telegram-reader /opt/telegram-reader/ /opt/telegram-reader/
COPY src/linear_tools/ /opt/hermes/plugins/council-linear/
COPY src/research_filing/ /opt/hermes/plugins/council-research/
RUN chmod -R a+rX,go-w /opt/hermes/plugins/council-secretary /opt/hermes/plugins/council-research /opt/agent-council/secretary_worker
RUN chmod -R a+rX,go-w /opt/agent-council/linear_agent /opt/hermes/plugins/council-linear
RUN chmod -R a+rX,go-w /opt/agent-council/council_team /opt/agent-council/council_recurring \
    /opt/agent-council/telegram_monitor /opt/hermes/council_team /opt/hermes/council_workspace \
    /opt/hermes/council_skills /opt/hermes/council_owner /opt/hermes/council_recurring \
    /opt/hermes/council_tasks /opt/hermes/research_filing /opt/hermes/plugins/council-workspace

COPY assets/login/ /opt/agent-council-theme/
COPY --from=web-assets /styles/node_modules/beauty-css/dist/beauty.min.css /opt/agent-council-theme/dependencies/beauty.min.css
COPY --from=web-assets /styles/node_modules/beauty-css/LICENSE /opt/agent-council-theme/dependencies/beauty-css.LICENSE
COPY --from=web-assets /styles/node_modules/beauty-css/package.json /opt/agent-council-theme/dependencies/beauty-package.json
COPY scripts/install-login-theme.py /opt/agent-council-theme/install.py
RUN PYTHONPATH=/opt/hermes python3 /opt/agent-council-theme/install.py /opt/hermes/hermes_cli/dashboard_auth/login_page.py /opt/agent-council-theme \
    && chmod -R a+rX,go-w /opt/agent-council-theme
