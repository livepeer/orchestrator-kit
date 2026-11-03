# Orchestrator Kit

Run a Livepeer orchestrator on your own machine and get paid jobs, without waiting on staff.

**Status:** pilot (LPA-252). The first template serves `community/hyperframes`, which renders HTML/CSS to MP4 on a CPU. The design, plan and scorecard live in livepeer/livepeer-agent#478.

## The whole journey

```bash
git clone https://github.com/livepeer/orchestrator-kit && cd orchestrator-kit
pip install -e .
mkdir -p ~/node && cp templates/hyperframes/lpo.yaml.example ~/node/lpo.yaml   # edit host and eth_address
lpo up --dir ~/node
```

`lpo up` renders the Kit from `lpo.yaml`, starts it, and runs `lpo doctor`. When something is missing, the doctor says where you are (one of seven stages: host → node → chain → runners → published → probation → live), what is wrong, and the next step. Fix it and run `lpo up` again. That is the only instruction.

Every run writes `lpo-report/<time>/report.md` and `report.json`. Paste `report.md` into your AI coding agent for step-by-step help. Reports never include your key, password or RPC URL.

## Your wallet

Put these files in `~/node/secrets/` yourself, mode 600. `lpo` checks that they exist and that the keystore matches `eth_address`. It never reads them out, copies them or prints them.

| File | What |
|---|---|
| `wallet-keystore` | the orchestrator's keystore JSON |
| `wallet-password` | its password |
| `arbitrum-rpc-url` | an Arbitrum One RPC URL |

## What is in the repo

| Path | What |
|---|---|
| `lpo/` | the CLI: `up`, `doctor`, `init`; `vocabulary.json` holds every stage, check, bar and fix |
| `templates/hyperframes/` | compose file (livepeer/node v1.0.5, Caddy, runner), Caddyfile, node config, `lpo.yaml.example` |
| `runners/hyperframes/` | the HyperFrames runner (Live Runner protocol) |
| `docs/pilot-hyperframes.md` | the LPA-252 pilot run, step by step |

## Tests

```bash
python3 -m pytest
```
