FROM python:3.11-slim

# Build argument for port with default value
ARG PORT=8001
ENV PORT=${PORT}

WORKDIR /app

# Copy the application code
COPY . .

# Install dependencies for adding NodeSource repo and Python dependencies
RUN apt-get update && \
    apt-get install -y curl gnupg --no-install-recommends && \
    # Add NodeSource repository for Node.js v18.x
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    # Install Node.js v18 (which includes npm and npx)
    apt-get install -y nodejs --no-install-recommends && \
    # Install Python dependencies
    pip install --no-cache-dir -r requirements.txt && \
    # Clean up
    apt-get purge -y --auto-remove curl gnupg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Expose the port from build argument
EXPOSE ${PORT}

# Command to run the application
CMD ["sh", "-c", "uvicorn mcp_agent_army_endpoint:app --host 0.0.0.0 --port ${PORT}"]
