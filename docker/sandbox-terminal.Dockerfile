# Terminal-backend tools and headless Chromium. No Hermes application, display
# server, remote desktop or background service runs inside this image.
ARG SANDBOX_BASE=nikolaik/python-nodejs:python3.13-nodejs26@sha256:b1599384efb3fb5f0e25930a9b9e8bd895cf78bb6dbb5f705f22c024db65f5dc
# hadolint ignore=DL3006
FROM ${SANDBOX_BASE}
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

RUN apt-get -o Acquire::Retries=3 update && \
    apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        jq ripgrep fd-find tmux less nano vim-tiny zip rsync tree procps htop \
        file bsdextrautils ca-certificates locales \
        libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 libcairo2 libcups2t64 \
        libdbus-1-3 libgbm1 libglib2.0-0t64 libnspr4 libnss3 libpango-1.0-0 \
        libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
        libxkbcommon0 libxrandr2 fonts-dejavu-core && \
    ln -sf /usr/bin/fdfind /usr/local/bin/fd && \
    rm -rf /var/lib/apt/lists/*

# The repository PM lock is the sole browser/tool artifact authority. Provision
# verified bytes once while building, never npm-fetch tools during a model call.
WORKDIR /opt/hermes-sandbox
ENV HERMES_RUNTIME_DIR=/opt/hermes-sandbox/tools
COPY pm/ pm/
COPY hermes_constants.py hermes_constants.py
COPY hermes_cli/__init__.py hermes_cli/runtime_state.py hermes_cli/
COPY scripts/bundles/payload.py scripts/bundles/payload.py
RUN python3 -c 'from pathlib import Path; from pm import ensure; [ensure(name, explicit=True) for name in ("agent-browser", "chromium")]; from pm import installed_package; [Path("/usr/local/bin", name).symlink_to(installed_package(name).binary) for name in ("agent-browser", "chromium")]' && \
    agent-browser --version && chromium --version && \
    chmod -R a+rX /opt/hermes-sandbox/tools && \
    printf 'AGENT_BROWSER_EXECUTABLE_PATH=/usr/local/bin/chromium\n' >> /etc/environment
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/local/bin/chromium
WORKDIR /workspace
CMD ["sleep", "infinity"]
