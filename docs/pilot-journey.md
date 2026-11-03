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
| 04:20 | H2 | owner decision: do not share the livebit.ca key; pilot uses a fresh wallet. VM `lpo.yaml` reset to the zero address | owner | |
| 04:25 | H3 (read only) | active set 100/100, lowest stake 610.2 LPT, top 4.53 M LPT; round length 6,377 L1 blocks (~21 h); unbonding 7 rounds | — | so a fresh operator needs ~1,000 LPT plus ~0.01 ETH |
| 04:30 | H2/H3 | node image ships `livepeer-chain` (no `livepeer_cli`): `account create` and `orchestrator register` (approve+bond+register+service URI, dry run by default) | — | Kit gains `lpo wallet` and `lpo register` on top of it (8c3c5d7) |
| 04:35 | H3 | smoke on the VM with a throwaway, unfunded key (deleted): create works; register dry run stops at "insufficient Livepeer token balance" | — | finding: an RPC URL file with a trailing newline is rejected; doctor now catches it (538ad6b) |

Waiting on the owner: `lpo wallet` on the VM (H2), then fund the address and `lpo register` (H3).
