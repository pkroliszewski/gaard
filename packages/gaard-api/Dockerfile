FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY packages /app/packages
COPY examples /app/examples

RUN pip install --no-cache-dir \
    -e /app/packages/gaard-plugin-api \
    -e /app/packages/gaard-core \
    -e /app/packages/gaard-connectors \
    -e /app/packages/gaard-llm \
    -e /app/packages/gaard-api

WORKDIR /app

EXPOSE 8000

CMD ["gaard-core", "start", "--host", "0.0.0.0", "--port", "8000"]
