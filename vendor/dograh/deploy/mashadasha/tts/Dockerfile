# Reuse the API image's shared layers, Python environment and ffmpeg.
# Run a separate tiny process; never start Dograh API services in this container.
FROM dograhai/dograh-api:1.48.0
USER root
COPY edge_tts-7.2.8-py3-none-any.whl tabulate-0.9.0-py3-none-any.whl SHA256SUMS /tmp/edge-wheel/
RUN cd /tmp/edge-wheel \
 && sha256sum -c SHA256SUMS \
 && /usr/local/bin/python -m pip install --no-index --no-deps --no-cache-dir --target /opt/edge-tts edge_tts-7.2.8-py3-none-any.whl tabulate-0.9.0-py3-none-any.whl \
 && rm -rf /tmp/edge-wheel
WORKDIR /tts
COPY --chown=dograh:dograh server.py /tts/server.py
ENV PYTHONPATH=/opt/edge-tts \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN python -c "import aiohttp,certifi,tabulate,typing_extensions,edge_tts; assert edge_tts.__version__ == '7.2.8'"
USER dograh
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
