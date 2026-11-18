# Pilot: a hyperframes node from a bare GCP VM (LPA-252 plan §4.1)

The first real end-to-end run. Every step records its time, every manual action and every doctor failure in `journey.md`, which becomes the "before" column of the M1 scorecard.

| Step | What | Who |
|---|---|---|
| H0 | VM in GCP project `naap-orchestrator` (outside the fleet; `livepeer-agent` has no billing): `e2-standard-8`, Ubuntu 24.04, public IP, ports 80/443 open, label `purpose=lpa-252-pilot` | Claude, after the owner's go |
| H1 | Install Docker; clone this repo; `pip install -e .`; write `lpo.yaml`; `lpo up` (renders, waits for the wallet files) | Claude |
| H2 | Fresh wallet (owner decision 2026-11-02: the livebit.ca key is not shared). Owner runs `lpo wallet` on the VM: types the password and RPC URL; the node image's `livepeer-chain` writes the keystore into `~/node/secrets/` (mode 600) and lpo fills `eth_address`. Owner backs up the keystore and password offline. Claude never handles them | owner |
| H3 | Owner funds the address on Arbitrum One (≈0.01 ETH, LPT above the active-set floor: 610.2 LPT on 2026-11-02, so ~1,000 LPT), runs `lpo register --lpt 1000` (dry run), then `--submit`: approve, bond, register (reward/fee cut 100), service URI `https://8-229-5-192.sslip.io`. Active from the next round (~21 h). Reward is optional for the pilot | owner |
| H4 | `lpo up` until every check passes; keep each report | Claude |
| H5 | Staging signer lists the node: simple-infra PR, folded into release R0 (planning only until R0) | Claude writes, owner applies |
| H6 | Agent catalog entry for `community/hyperframes` (planning only until M0) | Claude writes |
| H7 | Paid staging jobs by name under the gate cap; then bind to `hyperframes-render` | Claude |
| H8 | `journey.md`: time per stage, manual steps, staff touches, doctor failures | Claude |
| H9 | Delete the VM, or keep it as the M1 friendly node | owner decides |

Known gaps to close before M1:
- The runner renders untrusted HTML in headless Chromium. It refuses non-public background URLs, but page scripts can still fetch. Next step: no egress for the render process except the background fetch.
- The base image is pinned by tag, not digest, until the first pilot build.
- Bonding into a full active set evicts the lowest orchestrator; an operator a few LPT above the floor can be pushed out again by the next one.
