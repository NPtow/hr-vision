FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/interview-agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY services/recruiting-api/ /hrserver/
COPY services/interview-agent/runtime.py ./
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
ENTRYPOINT ["python", "runtime.py"]
