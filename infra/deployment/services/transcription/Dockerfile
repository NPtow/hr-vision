FROM python:3.12.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/asr:/app/source AWS_EC2_METADATA_DISABLED=true
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --require-hashes -r /app/requirements.lock \
    && groupadd --gid 10001 transcription \
    && useradd --uid 10001 --gid transcription --no-create-home transcription
COPY asr /app/asr
COPY source /app/source
USER 10001:10001
ENTRYPOINT ["/bin/sh", "-c", "umask 077; exec \"$@\"", "--"]
