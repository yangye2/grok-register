FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    wget \
    gnupg \
    ca-certificates \
    fonts-noto-cjk \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
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

