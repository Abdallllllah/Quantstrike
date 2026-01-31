# Build stage
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY app/ ./app/

# Create uploads directory for runtime (before switching to non-root user)
RUN mkdir -p uploads

# Create non-root user for security
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser


# Expose port
EXPOSE 8000

# Health check - uses the PORT env var which defaults to 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://localhost:{os.getenv(\"PORT\", 8000)}/api/health')" || exit 1

# Start the application - use shell form to read $PORT at runtime
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
