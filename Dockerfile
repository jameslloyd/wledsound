# ==============================================================================
# WLEDSOUND Dockerfile
# Multi-protocol audio reactive sync service for Music Assistant & WLED
# ==============================================================================

FROM python:3.12-slim

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system dependencies including snapclient
RUN apt-get update && apt-get install -y --no-install-recommends \
    snapclient \
    libasound2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY wledsound/ ./wledsound/
COPY config.example.yaml ./config.example.yaml

# Create directory for persistent config
RUN mkdir -p /config

EXPOSE 8080/tcp
EXPOSE 11988/udp
EXPOSE 4048/udp

# Healthcheck on web dashboard
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/api/status || exit 1

ENTRYPOINT ["python3", "-m", "wledsound.main", "/config/config.yaml"]
