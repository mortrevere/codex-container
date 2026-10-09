FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/usr/local/bin:/usr/bin:/bin"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install gh from GitHub's official apt repo so the CLI stays up to date
# (Ubuntu's bundled package can lag well behind upstream).
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

RUN /usr/local/bin/uv tool install ruff

RUN curl -fsSL https://chatgpt.com/codex/install.sh \
    | env CODEX_INSTALL_DIR=/usr/local/bin CODEX_HOME=/opt/codex CODEX_NON_INTERACTIVE=1 sh \
    && codex --version

# ntfy.sh notification hooks. The topic is provided at *runtime* via the
# CODEX_NTFY_TOPIC environment variable (see the codex-container wrapper),
# so the image itself stays generic and shareable. When the variable is empty,
# both scripts are a no-op.
RUN mkdir -p /usr/local/bin/codex-hooks \
    && printf '%s\n' \
        '#!/usr/bin/env bash' \
        '[ -z "${CODEX_NTFY_TOPIC:-}" ] && exit 0' \
        'curl --max-time 5 -fsS -d "Codex is waiting for me" "https://ntfy.sh/${CODEX_NTFY_TOPIC}" >/dev/null' \
        > /usr/local/bin/codex-hooks/notify-waiting.sh \
    && printf '%s\n' \
        '#!/usr/bin/env bash' \
        '[ -z "${CODEX_NTFY_TOPIC:-}" ] && exit 0' \
        'curl --max-time 5 -fsS -d "Codex is done" "https://ntfy.sh/${CODEX_NTFY_TOPIC}" >/dev/null' \
        > /usr/local/bin/codex-hooks/notify-done.sh \
    && chmod +x /usr/local/bin/codex-hooks/notify-waiting.sh /usr/local/bin/codex-hooks/notify-done.sh

WORKDIR /workspace
CMD ["bash"]
