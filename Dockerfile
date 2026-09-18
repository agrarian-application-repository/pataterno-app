# ------------------------------------------------------------
# pataterno-app — AGRARIAN Project
# Based on the AGRARIAN Dockerfile template (Python variant).
# ------------------------------------------------------------

FROM python:3.10-slim

WORKDIR /app

# Unbuffered stdout, so the DBCHECK startup line reaches `docker logs` /
# `kubectl logs` immediately even when nothing is attached to a terminal.
ENV PYTHONUNBUFFERED=1

# Which build is running - reported by /dbcheck and stamped on every ping row.
ARG IMAGE_TAG=dev
ENV IMAGE_TAG=${IMAGE_TAG}

# Install system dependencies (curl needed by the HEALTHCHECK)
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies.
# --only-binary turns a silent, 30-minute QEMU-emulated source build on the
# arm64 leg into an immediate, legible failure.
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

# Copy application code
COPY . .

# Mount point for an operator-supplied config override (/app/config/db.env);
# created before the user switch so it exists in the image.
RUN mkdir -p /app/config

# Create non-root user
RUN adduser --system --group appuser
USER appuser

# Expose port
EXPOSE 80

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl --fail http://localhost/health || exit 1

# Run application
CMD ["python", "src/app.py"]
