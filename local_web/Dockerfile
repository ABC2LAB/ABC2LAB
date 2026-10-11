FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /app

COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock.txt \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright

COPY modules/ ./modules/
COPY pipeline.py ./
COPY local_web/__init__.py local_web/server.py ./local_web/
COPY local_web/frontend/ ./local_web/frontend/

RUN useradd --uid 10001 --create-home web
USER web
EXPOSE 8080
CMD ["python", "-m", "local_web.server"]
