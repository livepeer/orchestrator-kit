# Pilot journey log (LPA-252 §4.1)

Times are UTC on 2026-11-03. "Manual" means a step a community operator would also do by hand today.

| Time | Step | Result | Manual? | Notes |
|---|---|---|---|---|
| 03:47 | H0 | `livepeer-agent` has no billing account, so Compute could not be enabled; switched to `naap-orchestrator` (owner) | staff | not an operator step; our own setup |
| 03:55 | H0 | VM `lpa-252-pilot-hf` (e2-standard-8, Ubuntu 24.04, us-west1-b, 8.229.5.192), firewall `lpa-252-pilot-web` (80/443, tag `lpa-252-pilot`) | yes | host name `8-229-5-192.sslip.io`, so no DNS step |
| 03:56–03:57 | H1 | Docker 29.9.2 + compose v5.7.0 installed (1 min 21 s) | yes | one script; should become part of `lpo up` on a bare host |
| 03:57 | H1 | `lpo up` → refuses: `orchestrator.eth_address is not a wallet address`; `lpo doctor` → stage 2/7 `node`, exit 1, report written | yes | correct stop; waiting for the owner's wallet |
| 03:58 | H1 | runner image build failed: livepeer-gateway needs Python ≥ 3.12, base had 3.11 | — | Kit bug, fixed (Python 3.12 base + Node 22); caught on the first real build |
| 04:03 | H4 (partial) | first real render in the runner image: HTTP 200 in 8.5 s; MP4 h264 1920×1080, 3.0 s, 90 frames | — | renderer works on CPU; node and payment path not yet involved |
| 04:04 | H2 | owner gave `0x2cC8…1328`; `lpo.yaml` filled, Kit rendered; `lpo up` waits for the wallet files | yes | |
| 04:05 | H3 (read only) | address is already an active, registered orchestrator: 17,086.57 LPT bonded, round 4392, on-chain service URI `https://livebit.ca:8935` | — | no bond/activate needed; changing the URI would move that node's traffic |
| 04:05 | H3 | public Arbitrum RPC returns 403 without a User-Agent | — | Kit bug, fixed in `lpo` (8d935a0) |
| 04:06 | H3 | new non-blocking check `chain.service_uri` warns when the on-chain URI names another host | — | covers "second host for an existing orchestrator" |
| 04:06 | H2 | doctor: stage 2/7, 7 blocking; its "Next step" points at container logs, though the real blocker is the missing wallet files | — | finding: next step should follow the root cause (`chain.keystore`) |

Waiting on the owner: decide how this address is shared with livebit.ca (see plan §4.1), then place the keystore, password and RPC files on the VM (H2).
