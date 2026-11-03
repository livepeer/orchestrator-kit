# HyperFrames runner (Live Runner app): HTML/CSS to MP4 with headless Chromium and ffmpeg. CPU only.
# Pin the base by digest after the first pilot build (docs/pilot-hyperframes.md, step H1).
# livepeer-gateway needs Python >= 3.12 and hyperframes needs Node >= 22: Python base (the fleet's fal-app digest), Node copied in.
FROM docker.io/library/node:22-bookworm-slim AS node

FROM docker.io/library/python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && node --version

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg chromium fonts-dejavu-core fonts-noto-color-emoji ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Same livepeer-gateway commit as the fleet's fal-app (register_runner works with node v1.0.5).
RUN pip install "aiohttp>=3.9,<4" \
    "livepeer-gateway @ git+https://github.com/livepeer/livepeer-python-gateway@61061410ce760eee4e680f3447cc6982cae667d7" \
    && python -c "from livepeer_gateway.live_runner import register_runner"

RUN npm install -g hyperframes@0.9.6 && hyperframes --version

WORKDIR /app
COPY app.py composition.py ./

RUN useradd --system --uid 1000 --create-home runner && mkdir -p /outputs && chown runner /outputs
USER runner

EXPOSE 8993
CMD ["python", "app.py"]
