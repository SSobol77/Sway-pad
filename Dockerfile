# Use official Python runtime as parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Create app directory
WORKDIR /usr/src/sway-pad

# Copy project files
COPY setup.py ./
COPY pyproject.toml ./
COPY sway_pad/ ./sway_pad/
COPY config.toml ./sway_pad/
COPY tests/ ./tests/
COPY docs/ ./docs/
COPY examples/ ./examples/
COPY packaging/ ./packaging/

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libncurses5-dev \
    libncursesw5-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -U pip wheel
RUN pip install --no-cache-dir .[dev]

# Create non-root user and switch to it
RUN adduser --disabled-password --gecos "" appuser
USER appuser

# Expose port (if needed)
EXPOSE 8080

# Define default command
CMD ["python", "sway_pad/sway.py"]
