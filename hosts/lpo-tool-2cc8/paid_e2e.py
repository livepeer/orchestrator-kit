"""Direct paid test of lpo-tool-2cc8 through a Livepeer signer (no Agent routing).

Calls the node's runners the way the agent-fleet SDK does (livepeer_gateway call_runner,
the SDK's pinned gateway commit). The signer is paid by the caller's Clearinghouse key.
Then it proves on chain that fees reached the orchestrator address:
- WinningTicketRedeemed events with recipient 0x2cC8;
- BondingManager pendingFees(0x2cC8) rising;
- the hot wallet's balance falling by the redemption gas.

    python paid_e2e.py --key-file ~/.config/livepeer-qa/prod-lpg-key --calls 10

The key is read from the file (mode 600) and never printed. Every call is capped by --max-usd.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

NODE = "https://136-118-245-156.sslip.io"
ORCH = "0x2cC8AeC211Fdc372c0F7E4d1add1675733381328"
HOT = "0xaf70fcbfdbd1f28fdf3a89220f605b30d69ad413"
RPC = "https://arb1.arbitrum.io/rpc"
BONDING_MANAGER = "0x35Bcf3c30594191d53231E4FF333E8A770453e40"
ROUNDS_MANAGER = "0xdd6f56DcC28D3F5f27084381fE8Df634985cc39f"
TICKET_BROKER = "0xa8bB618B1520E284046F3dFc448851A1Ff26e41B"
WINNING_TICKET_REDEEMED = "0xc389eb51ed006dbf2528507f010efdf5225ea596e1e1741d74f550dab1925ee7"
PRICE_USD = 0.015

HTML = (
    '<html><body style="margin:0;background:#101820;color:#fff;font:56px sans-serif;'
    'display:flex;align-items:center;justify-content:center;height:100vh">'
    "lpo-tool-2cc8 paid test {n}</body></html>"
)


def cast(*args: str) -> str:
    return subprocess.run(["cast", *args, "--rpc-url", RPC], check=True, capture_output=True, text=True).stdout.strip()


def chain_state() -> dict:
    rnd = cast("call", ROUNDS_MANAGER, "currentRound()(uint256)").split()[0]
    fees = cast("call", BONDING_MANAGER, "pendingFees(address,uint256)(uint256)", ORCH, rnd).split()[0]
    return {
        "block": int(cast("block-number")),
        "round": int(rnd),
        "pending_fees_wei": int(fees),
        "hot_balance_wei": int(cast("balance", HOT)),
    }


def redemptions(from_block: int) -> list[dict]:
    recipient = "0x" + ORCH[2:].lower().rjust(64, "0")
    out = cast("logs", "--from-block", str(from_block), "--address", TICKET_BROKER,
               WINNING_TICKET_REDEEMED, "--json")
    events = [e for e in json.loads(out or "[]") if len(e["topics"]) > 2 and e["topics"][2].lower() == recipient]
    return [{"tx": e["transactionHash"], "block": int(e["blockNumber"], 16),
             "face_value_wei": int(e["data"][2:66], 16)} for e in events]


def runners() -> dict[str, str]:
    with urllib.request.urlopen(f"{NODE}/discovery", timeout=20) as r:
        node = json.load(r)[0]
    return {x["app"]: x["url"] for x in node["runners"]}


def read_key(path: Path) -> str:
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        sys.exit(f"{path} must be mode 600")
    key = path.read_text().strip()
    if not key.startswith("lpg_"):
        sys.exit(f"{path} does not hold an lpg_ key")
    return key


async def one(call_runner, url: str, cap: str, payload: dict, signer: str, headers: dict) -> dict:
    t = time.time()
    try:
        res = await call_runner(runner_url=f"{url}/{cap}", payload=payload, signer_url=signer,
                                signer_headers=headers, timeout=180)
        data = res.data if isinstance(res.data, dict) else {}
        media = (data.get("data") or data).get("url")
        return {"cap": cap, "ok": bool(media), "s": round(time.time() - t, 1), "url": media,
                "session_id": res.session_id}
    except Exception as err:  # report and continue; the summary counts failures
        return {"cap": cap, "ok": False, "s": round(time.time() - t, 1), "error": f"{type(err).__name__}: {err}"[:300]}


async def run(args) -> int:
    from livepeer_gateway.live_runner import call_runner

    if args.calls * 2 * PRICE_USD > args.max_usd:
        sys.exit(f"{args.calls * 2} calls x ${PRICE_USD} is above --max-usd {args.max_usd}")
    headers = {"Authorization": f"Bearer {read_key(Path(os.path.expanduser(args.key_file)))}"}
    apps = runners()
    before = chain_state()
    print("before", json.dumps(before))
    results = []
    for n in range(args.calls):
        hf = await one(call_runner, apps["hyperframes-render"], "hyperframes-render",
                       {"html": HTML.format(n=n), "duration": 3, "width": 640, "height": 360}, args.signer, headers)
        results.append(hf)
        print(json.dumps(hf))
        if hf["ok"]:
            trim = await one(call_runner, apps["ffmpeg-trim"], "ffmpeg-trim",
                             {"source_url": hf["url"], "start_sec": 0, "duration_sec": 1.5}, args.signer, headers)
            results.append(trim)
            print(json.dumps(trim))
    ok = sum(r["ok"] for r in results)
    print(f"calls ok {ok}/{len(results)}; waiting {args.wait}s for redemptions")
    time.sleep(args.wait)
    after = chain_state()
    won = redemptions(before["block"])
    summary = {
        "calls_ok": ok,
        "calls": len(results),
        "billed_usd_at_list": round(ok * PRICE_USD, 3),
        "winning_tickets_to_0x2cC8": won,
        "pending_fees_delta_wei": after["pending_fees_wei"] - before["pending_fees_wei"],
        "hot_wallet_gas_wei": before["hot_balance_wei"] - after["hot_balance_wei"],
        "after": after,
    }
    print("summary", json.dumps(summary, indent=2))
    return 0 if ok == len(results) and won and summary["pending_fees_delta_wei"] > 0 else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--key-file", required=True)
    p.add_argument("--signer", default="https://signer.prod.livepeer.live")
    p.add_argument("--calls", type=int, default=10, help="HyperFrames renders; each is followed by one ffmpeg-trim")
    p.add_argument("--max-usd", type=float, default=0.30)
    p.add_argument("--wait", type=int, default=120, help="seconds to wait for the node to redeem winners")
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
