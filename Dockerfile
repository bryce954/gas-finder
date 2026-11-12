# FuelScope — Docker image for gas price finder
# Build: docker build -t gas-finder .
# Run: docker run -p 5002:5002 gas-finder

FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash app
WORKDIR /home/app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=app:app . .

# Create cache directory with proper permissions for SQLite
RUN mkdir -p /home/app/.cache/gas_finder && chown -R app:app /home/app/.cache

# Switch to non-root user
USER app

# Expose port
EXPOSE 5002

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5002/api/sources || exit 1

# Run the app
CMD ["python", "app.py"]