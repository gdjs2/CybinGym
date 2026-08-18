#!/usr/bin/env bash
set -euo pipefail

# ========================
# MCP Server 1: idalib-mcp
# ========================
IDA_MCP_PORT=${IDA_MCP_PORT:-8002}
uv run idalib-mcp --host 0.0.0.0 --port ${IDA_MCP_PORT} >/root/idalib-mcp.log 2>&1 &

# ==========================
# MCP Server 2: pyghidra-mcp
# ==========================
PYGHIDRA_MCP_PORT=${PYGHIDRA_MCP_PORT:-8003}
GHIDRA_INSTALL_DIR="/opt/ghidra/" pyghidra-mcp \
  -t sse -o 0.0.0.0 -p ${PYGHIDRA_MCP_PORT} >/root/pyghidra-mcp.log 2>&1 &

exec "$@"
