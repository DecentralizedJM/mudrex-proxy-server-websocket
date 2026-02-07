# =============================================================================
# Mudrex WebSocket Proxy Server - Dockerfile
# Production-optimized multi-stage build
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Build
# -----------------------------------------------------------------------------
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2: Production
# -----------------------------------------------------------------------------
FROM python:3.11-slim as production

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

# Copy application code and start script
COPY --chown=appuser:appuser app/ /app/app/
COPY --chown=appuser:appuser scripts/start.sh /app/scripts/start.sh
RUN chmod +x /app/scripts/start.sh

# Switch to non-root user
USER appuser

# Expose port (Railway uses PORT env at runtime)
EXPOSE 8000

# Run via script so PORT from Railway is always used
CMD ["/app/scripts/start.sh"]
