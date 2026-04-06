# Gunakan Python 3.11 versi slim yang stabil & hemat resource
FROM python:3.11-slim as builder

# Menonaktifkan cache pip dan mencegah pyc files intervensi
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies untuk library Machine Learning (LightGBM/pandas)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies utama
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Jendela Produksi ---
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Salin library runtime yang butuh libgomp1 (diperlukan oleh LightGBM dkk)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy packages dari builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Salin kode agen & file dukungan
COPY runtime/ ./runtime/
COPY automation/ ./automation/
COPY observability/ ./observability/
COPY audit/ ./audit/
COPY research/ ./research/

# Buat direktori esensial (volume)
RUN mkdir -p /app/logs /app/runtime/agent/memory /app/audit /app/research/data /app/backup/db

# Port untuk expose Metrics Server
EXPOSE 8090

# Default Command dijalankan: Agent Utama
CMD ["python", "runtime/agent/core/main.py"]
