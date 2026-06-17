FROM ubuntu:24.04

# Install basic reverse engineering and scripting tools for the agent
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    openjdk-21-jdk-headless \
    build-essential \
    gdb \
    binutils \
    file \
    strace \
    curl \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Download and install Ghidra 12.1.2 to /opt
RUN set -eux; \
    GHIDRA_URL="https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.2_build/ghidra_12.1.2_PUBLIC_20260605.zip"; \
    curl -fsSL "$GHIDRA_URL" -o /tmp/ghidra.zip; \
    unzip -q /tmp/ghidra.zip -d /opt; \
    rm -f /tmp/ghidra.zip

# Expose Ghidra headless tools (e.g., analyzeHeadless) on PATH
ENV PATH="/opt/ghidra_12.1.2_PUBLIC/support:${PATH}"

# Inspect automatically sets the working directory and mounts the files