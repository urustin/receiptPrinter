FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" python-escpos psycopg2-binary pillow \
    authlib httpx "python-jose[cryptography]" itsdangerous python-dotenv

COPY app.py printer.py auth.py jira.py ./

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
