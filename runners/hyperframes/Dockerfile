# HyperFrames runner (Live Runner app): HTML/CSS to MP4 with headless Chromium and ffmpeg. CPU only.
# Pin the base by digest after the first pilot build (docs/pilot-hyperframes.md, step H1).
FROM docker.io/library/node:22-bookworm-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_BREAK_SYSTEM_PACKAGES=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg chromium fonts-dejavu-core fonts-noto-color-emoji ca-certificates git python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Same livepeer-gateway commit as the fleet's fal-app (register_runner works with node v1.0.5).
RUN pip3 install "aiohttp>=3.9,<4" \
    "livepeer-gateway @ git+https://github.com/livepeer/livepeer-python-gateway@61061410ce760eee4e680f3447cc6982cae667d7" \
    && python3 -c "from livepeer_gateway.live_runner import register_runner"

RUN npm install -g hyperframes@0.9.6 && hyperframes --version

WORKDIR /app
COPY app.py composition.py ./

RUN useradd --system --uid 1000 --create-home runner && mkdir -p /outputs && chown runner /outputs
USER runner

EXPOSE 8993
CMD ["python3", "app.py"]
