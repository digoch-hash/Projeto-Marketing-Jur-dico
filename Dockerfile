FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# O agendador, a trava de login e a reserva de publicacao rodam dentro do processo:
# use SEMPRE um unico worker (nao aumente --workers).
CMD ["sh", "-c", "exec uvicorn app.web.main:app_factory --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
