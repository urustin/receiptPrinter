FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask python-escpos psycopg2-binary

COPY app.py .
COPY templates/ templates/
COPY static/ static/

EXPOSE 5000

CMD ["python3", "app.py"]
