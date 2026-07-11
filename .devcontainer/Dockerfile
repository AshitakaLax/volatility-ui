FROM python:3.11-slim

# Install system dependencies and uv package manager for AI agents
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# Add uv path to environment variables
ENV PATH="/root/.cargo/bin:${PATH}"

# Set up the working directory
WORKDIR /workspace

# Copy requirements and install
COPY pyproject.toml .
