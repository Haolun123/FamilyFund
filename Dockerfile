FROM python:3.13-slim

# Chinese font for matplotlib chart rendering
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

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "dashboard/app.py"]
