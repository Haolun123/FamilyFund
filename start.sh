#!/bin/bash
set -e

# 启动 MCP Server（后台）
python /app/mcp_server.py &
MCP_PID=$!
echo "MCP server started (pid $MCP_PID) on :5174"

# 启动静态文件服务（Finance Reports PDF 浏览，后台）
REPORTS_DIR="/app/data/Finance Reports"
if [ -d "$REPORTS_DIR" ]; then
    python -m http.server 5175 --directory "$REPORTS_DIR" --bind 0.0.0.0 &
    echo "Reports file server started on :5175"
fi

# 启动 Streamlit（前台，保持容器活着）
exec streamlit run dashboard/app.py \
  --server.port=8501 \
  --server.address=0.0.0.0 \
  --server.headless=true
