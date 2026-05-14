FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (runtime only)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY dashboard/ dashboard/
COPY .streamlit/ .streamlit/
COPY data/portfolio_sample.csv data/portfolio_sample.csv

# Install CJK font for matplotlib PDF rendering
# Font file must exist locally at assets/fonts/ArialUnicodeMS.ttf
# (not tracked in git, copy from /System/Library/Fonts/Supplemental/Arial Unicode.ttf on macOS)
COPY assets/fonts/ArialUnicodeMS.ttf /usr/local/lib/python3.13/site-packages/matplotlib/mpl-data/fonts/ttf/ArialUnicodeMS.ttf
RUN python3 -c "import matplotlib.font_manager as fm; fm._load_fontmanager(try_read_cache=False); print('Font cache rebuilt')"

EXPOSE 8501
EXPOSE 5174

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

COPY mcp_server.py /app/mcp_server.py
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENTRYPOINT ["/app/start.sh"]
