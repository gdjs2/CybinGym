ARG BASE_IMAGE=ubuntu:16.04

FROM ${BASE_IMAGE}

RUN sed -i 's/^mesg n.*/if tty -s; then mesg n; fi/g' /root/.profile || true

# Install basic reverse engineering and scripting tools for the agent
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    build-essential \
    gdb \
    binutils \
    file \
    strace \
    curl \
    unzip \
    ca-certificates \
    vim-common \
    git \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/opt/java/openjdk

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) jdk_arch='x64' ;; \
        arm64) jdk_arch='aarch64' ;; \
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    JDK_URL="https://api.adoptium.net/v3/binary/latest/21/ga/linux/${jdk_arch}/jdk/hotspot/normal/eclipse"; \
    mkdir -p /opt/java; \
    curl -fsSL "$JDK_URL" -o /tmp/jdk21.tar.gz; \
    tar -xzf /tmp/jdk21.tar.gz -C /opt/java; \
    rm -f /tmp/jdk21.tar.gz; \
    mv /opt/java/jdk-* "$JAVA_HOME"

# Download and install Ghidra 12.1.2 to /opt
RUN set -eux; \
    GHIDRA_URL="https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.2_build/ghidra_12.1.2_PUBLIC_20260605.zip"; \
    curl -fsSL "$GHIDRA_URL" -o /tmp/ghidra.zip; \
    unzip -q /tmp/ghidra.zip -d /opt; \
    rm -f /tmp/ghidra.zip

WORKDIR /CybinGym_workdir

# Expose Ghidra headless tools (e.g., analyzeHeadless) on PATH
ENV PATH="${JAVA_HOME}/bin:/opt/ghidra_12.1.2_PUBLIC/support:${PATH}"

# Inspect automatically sets the working directory and mounts the files