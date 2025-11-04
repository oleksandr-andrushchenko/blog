# Use Python 3.12 slim
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install make and zip
RUN apt-get update && apt-get install -y zip && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Default command
CMD ["tail", "-f", "/dev/null"]