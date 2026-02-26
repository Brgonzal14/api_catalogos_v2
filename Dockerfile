FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# gunicorn para múltiples workers (performance)
RUN pip install --no-cache-dir gunicorn

COPY app ./app

ENV PYTHONPATH=/app

# 2 workers suele ir bien en PCs normales; si el PC es potente, sube a 3-4.
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "app.main:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]