# lpo-tool-2cc8

An independent tool orchestrator for 0x2cC8…1328 that serves ffmpeg and HyperFrames, priced per call (USD, `fixed`): each ffmpeg tool at the agent-fleet tool hosts' own network price, and HyperFrames at $0.015. It is set up and operated outside simple-infra. Its configuration is the agent-fleet tool host's, produced by the fleet's own renderer, with three listed differences.

| File | What |
|---|---|
| `render_bundle.py` | Runs the agent-fleet renderer from `git archive de55cce` of a simple-infra checkout next to the Kit. It writes the files below. `--check` fails if they are stale. |
| `compose.yaml`, `config.toml`, `Caddyfile`, `host.json` | Renderer output (do not hand-edit) |
| `capabilities.json` | The fleet's 16 ffmpeg rows plus `hyperframes-render`, each with its price (`price_usd` = `display_price_usd`) |
| `test_bundle.py` | Pins the output, the price, the Agent capability names, the payout address and ticket EV ≤ the signer's cap |

## Differences from an agent-fleet tool host (all in `render_bundle.py`)

1. **`tool-hyperframes`** is added with its pre-#496 fleet limits (2 CPU, 2g, pids 1024, profile `hyperframes`). It is last in the list, so existing containers keep their addresses.
2. **Payments.** The Node uses the fleet's paid-orchestrator lines:
   - `OrchestratorAddress` 0x2cC8, network `arbitrum-one-mainnet`;
   - keystore, password and RPC URL as files;
   - ticket EV 6e12 wei.

   Face value is 3e13 wei for the paid test (about 1 ticket in 5 wins) and 4e14 wei steady (the fleet's); set `TICKET_MODE`. All fleet payment checks run, except the rule that tool hosts stay offchain.
3. **Price (owner decision 2026-11-05, simple-infra#570 review point 4).** Each ffmpeg tool costs what the fleet's paid tool hosts charge for it: `per_call_usd` in simple-infra `agent-fleet/capabilities/catalogue-tool-prices.json` at `PRICES_COMMIT` (the Agent catalogue price less the 5% house margin; `ffmpeg-trim` $0.002381, `ffprobe-info` $0.000476). `hyperframes-render` has no catalogue price and stays at $0.015. With equal prices the SDK shares traffic between this host and the fleet at no extra cost. The rows carry `price_usd`, which the release `tool-app` (#570) registers. `LR_TOOL_PRICES_JSON` and the flat `LR_TOOL_PRICE_USD` are for the overlay image. The ticket EV is the fleet's tool ticket, 2e11 wei (about $0.0005), so a cheap call never pays a whole $0.015 ticket.

## Images (registry `us-west1-docker.pkg.dev/naap-orchestrator/lpo-tool`)

| Image | Source |
|---|---|
| `livepeer/node` v1.0.5, Caddy 2.11.7 | Docker Hub, the fleet's digests |
| `tool-ffmpeg@sha256:885289bf…` | the agent-fleet prod pin (simple-infra `1981c89a`, release 2026.11.16-1945Z), copied with the same digest; was `38b02a95…` until 2026-11-16 (LPA-516) |
| `tool-app-priced@sha256:22ebeee8…` | the agent-fleet staging `tool-app@sha256:7dccec1a…` plus one file (`../../images/tool-app-priced`) |
| `tool-hyperframes` | `../../images/tool-hyperframes`: simple-infra `b7fa98e^` source, fleet image style |

## Storage

- Results go to `gs://lpo-tool-2cc8-outputs` (project `naap-orchestrator`, US-WEST1): public read; writers `lpo-tool-2cc8@` and `lpo-tool-livebit@naap-orchestrator.iam.gserviceaccount.com`; 7-day soft delete.
- **Delete rule: 30 days**, from `lifecycle.json`, the same as the fleet's tool-output bucket. It is not set at boot (the VM cannot administer the bucket): an operator runs `./apply_bucket_lifecycle.sh`, and `./apply_bucket_lifecycle.sh --check` reports drift. It was a hand-set 2 days until LPA-527, which deleted users' results before the 7-day window the Beta Terms promise.
- From LPA-527 the fleet SDK copies every result into its own private bucket when it returns, so this bucket only matters for links stored before that release.

## Runtime (on the VM, `/opt/lpo/host`)

- `secrets/`, mode 0400: `wallet-keystore`, `wallet-password` and `arbitrum-rpc-url` come from Secret Manager (`lpo-tool-2cc8-*`). `runner-bootstrap` is generated on the VM.
- `.env` holds `ORCH_SECRET` (= `runner-bootstrap`) and `GCS_BUCKET=lpo-tool-2cc8-outputs`.
- Start: `docker compose --profile media --profile hyperframes up -d`.
