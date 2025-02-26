FROM python:3.11-slim

WORKDIR /app

# Install supervisord
RUN apt-get update && apt-get install -y supervisor

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy your application code
COPY . .

# Copy supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create log directory
RUN mkdir -p /var/log/supervisor

# Ensure environment variables are passed to supervisor
ENV PYTHONUNBUFFERED=1

# Run supervisord
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]