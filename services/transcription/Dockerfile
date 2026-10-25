FROM python:3.12.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 transcription \
    && useradd --uid 10001 --gid transcription --no-create-home transcription \
    && mkdir -p /var/lib/transcription \
    && chown transcription:transcription /var/lib/transcription
COPY src ./src
USER 10001:10001
ENV TRANSCRIPTION_DATA_DIR=/var/lib/transcription
EXPOSE 8080
CMD ["python", "-m", "md_transcription", "api", "--host", "0.0.0.0"]
