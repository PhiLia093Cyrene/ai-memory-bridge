FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

EXPOSE 8000

# Run with uvicorn (1 worker is fine, this is I/O bound)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
