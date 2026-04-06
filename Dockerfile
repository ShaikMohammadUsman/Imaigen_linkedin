FROM python:3.11-slim

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies first (for caching layers)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers and system deps
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

# Expose port (FastAPI default)
EXPOSE 8000

# Set environment variables for production
ENV PYTHONUNBUFFERED=1
ENV HEADLESS=true

# Command to run the application
CMD ["uvicorn", "ui_server:app", "--host", "0.0.0.0", "--port", "8000"]
