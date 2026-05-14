#!/bin/bash
set -e

# 启动 MCP Server（后台）
python /app/mcp_server.py &
MCP_PID=$!
echo "MCP server started (pid $MCP_PID) on :5174"

# 启动 Streamlit（前台，保持容器活着）
exec streamlit run dashboard/app.py \
  --server.port=8501 \
  --server.address=0.0.0.0 \
  --server.headless=true
