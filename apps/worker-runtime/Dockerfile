FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    xvfb \
    ca-certificates \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt /tmp/requirements.txt
COPY apps/console/requirements.txt /tmp/console-requirements.txt
RUN pip install --no-cache-dir \
    -r /tmp/requirements.txt \
    -r /tmp/console-requirements.txt \
    && rm -f /tmp/requirements.txt /tmp/console-requirements.txt

COPY config.example.json /workspace/config.example.json
COPY DrissionPage_example.py email_register.py /workspace/
COPY turnstilePatch /workspace/turnstilePatch
COPY apps/console /workspace/apps/console

ENV GROK_REGISTER_SOURCE_DIR=/workspace \
    GROK_REGISTER_PYTHON=/usr/local/bin/python \
    GROK_REGISTER_CONSOLE_HOST=0.0.0.0 \
    GROK_REGISTER_CONSOLE_PORT=18600

EXPOSE 18600

CMD ["python", "/workspace/apps/console/app.py"]
