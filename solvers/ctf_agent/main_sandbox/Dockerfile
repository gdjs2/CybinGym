# CTF analysis image assembled from locally cached OpenSAGE tool images.
#
# The Kali package mirror is not reliable from Docker in the Inspect runner.
# The OpenSAGE tool images are already pulled locally and contain the large
# binary-analysis payloads needed here.
FROM ghcr.io/opensage-agent/sagemath:latest

ENV TERM=xterm
ENV NO_COLOR=1
ENV PATH="/root/.local/bin:$PATH"

# --- uv / Python env ---
RUN wget -qO- https://astral.sh/uv/install.sh | sh
RUN mkdir -p /app
RUN uv venv --python 3.12 --seed /app/.venv
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"
RUN uv pip install --python /app/.venv/bin/python \
      "protobuf==3.20.0" \
      "numpy>=1.26,<2" \
      scipy \
      neo4j \
      setuptools \
      angr \
      capstone \
      ROPGadget \
      lief \
      z3-solver \
      mcp \
      requests \
      playwright \
      pwntools \
      chepy \
      "git+https://github.com/ziyue-pan/pyghidra-mcp.git" \
      "https://github.com/mrexodia/ida-pro-mcp/archive/refs/heads/main.zip"


# --- Playwright ---
RUN /app/.venv/bin/python -m playwright install chromium

# --- ida-pro + ida-pro-mcp ---
COPY --from=ghcr.io/opensage-agent/ida-pro:latest /opt/ida /opt/ida
COPY --from=ghcr.io/opensage-agent/ida-pro:latest /opt/lumina /opt/lumina
RUN cd /opt/ida && /app/.venv/bin/python idakeygen.py && \
    cd idalib/python && uv pip install --python /app/.venv/bin/python . && \
    /app/.venv/bin/python py-activate-idalib.py -d /opt/ida
COPY --from=ghcr.io/opensage-agent/ida-pro:latest /root/ida.reg /root/.idapro/
ENV PATH="/opt/ida:/opt/lumina:$PATH"

# --- ghidra + ghidra-mcp ---
COPY --from=ghcr.io/opensage-agent/ghidra:latest /opt/ghidra /opt/ghidra
COPY --from=ghcr.io/opensage-agent/ghidra:latest /opt/java /opt/java
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="/opt/java/openjdk/bin:$PATH"

# --- sagemath ---
ENV PATH="$PATH:/opt/sagemath/bin"

# --- Entry ---
COPY entrypoint.sh /root/entrypoint.sh
RUN chmod +x /root/entrypoint.sh

# --- Expose MCP ports ---
EXPOSE 8002
EXPOSE 8003
EXPOSE 8004

ENTRYPOINT ["/root/entrypoint.sh"]
WORKDIR /workspace
