# Use Node.js v20 as base image
FROM node:20

# Install Python 3.11 (needed for the main application)
# node:20 is Debian-based (Bookworm)
RUN apt-get update && \
    apt-get install -y python3.11 python3-pip python3.11-venv --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency definitions (Python and Node.js)
COPY requirements.txt ./
COPY package*.json ./

# Install Node.js dependencies (MCP Servers)
RUN npm install

# Install Python dependencies
# Create a virtual environment
RUN python3.11 -m venv /app/venv
# Activate venv and install requirements
# Note: We run pip install within the RUN command, venv doesn't persist between RUN layers easily
RUN . /app/venv/bin/activate && pip install --no-cache-dir -r requirements.txt

# Ensure required directory exists for filesystem agent
RUN mkdir -p /app/local_files

# Copy the rest of the application code
COPY . .

# Set environment variables (can be overridden by Railway)
ENV PORT=8001
ENV LOCAL_FILE_DIR=/app/local_files
# Ensure venv python is used
ENV PATH="/app/venv/bin:$PATH"

# Expose the port
EXPOSE ${PORT}

# Command to run the application using the venv python
CMD ["uvicorn", "mcp_agent_army_endpoint:app", "--host", "0.0.0.0", "--port", "${PORT}"]
