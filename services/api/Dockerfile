FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY packages /app/packages
COPY services/api /app/services/api
COPY examples /app/examples
COPY apps /app/apps

RUN pip install --no-cache-dir \
    -e /app/packages/gaard-core \
    -e /app/packages/gaard-connectors \
    -e /app/packages/gaard-llm \
    -e /app/services/api

WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "services.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
