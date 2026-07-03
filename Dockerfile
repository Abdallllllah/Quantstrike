FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY migrations/ ./migrations/

# Create uploads directory
RUN mkdir -p uploads

# Expose port
EXPOSE 8000

# Run the application. Shell form so $PORT (set by Render) is honored; the
# in-process self-calls in the gateway target 127.0.0.1:$PORT, so binding the
# same port keeps everything on one service.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
