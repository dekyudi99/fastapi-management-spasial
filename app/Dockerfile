FROM python:3.11-slim

WORKDIR /app

# Install dependency sistem: libpq buat PostGIS client, libexpat & lainnya buat GDAL/rasterio
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libexpat1 \
    libgdal32 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]