# Production Dockerfile for Swagbucks Multi Tracker with Playwright support
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Set environment defaults for cloud hosting
ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HEADLESS=true \
    MAX_CONCURRENT_CHECKS=2

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser binary
RUN playwright install chromium

# Copy application source code
COPY . .

# Ensure data and session directories exist
RUN mkdir -p data browser_sessions

# Expose port (Render, HF Spaces, Koyeb)
EXPOSE 8080

# Production Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Start FastAPI application
CMD ["python", "web_app.py"]
