FROM python:3.11-slim

# Build argument for port with default value
ARG PORT=8001
ENV PORT=${PORT}

WORKDIR /app

# Copy the application code
COPY . .

# Install Node.js, npm (which includes npx), and Python dependencies
RUN apt-get update && \
    apt-get install -y nodejs npm --no-install-recommends && \
    npm install -g npx && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Expose the port from build argument
EXPOSE ${PORT}

# Command to run the application
CMD ["sh", "-c", "uvicorn mcp_agent_army_endpoint:app --host 0.0.0.0 --port ${PORT}"]
