"""Render lpo-tool-2cc8's host files with the agent-fleet renderer, unchanged, at FLEET_COMMIT.

The host is an agent-fleet tool host (same images, compose, limits, network, Caddy and Node
config) with three deliberate differences, all made here and nowhere else:

1. tool-hyperframes is added to the tool host class, with the limits it had in the fleet
   before simple-infra #496 removed it (b7fa98e^ render/classes.py). It goes last, so every
   existing container keeps its address.
2. Payments: the Node gets the fleet's paid-orchestrator lines (render/node.py), with
   OrchestratorAddress 0x2cC8. Every fleet payment check runs, except "tool hosts stay
   offchain", which is the rule this host exists to change.
3. tool-app advertises a price per call (read by the tool-app-priced overlay image): each ffmpeg
   tool at the fleet's own network price for it (simple-infra catalogue-tool-prices.json at
   PRICES_COMMIT: the Agent catalogue price less the 5% house margin), hyperframes-render (no
   catalogue price) at the flat LR_TOOL_PRICE_USD.

Nothing is read from or written to simple-infra except `git archive` of FLEET_COMMIT.

    python3 render_bundle.py          # write the files next to this script
    python3 render_bundle.py --check  # exit 1 if the committed files differ
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMPLE_INFRA = HERE.parents[2] / "simple-infra"
FLEET_COMMIT = "de55cce"

DOMAIN = "136-118-245-156.sslip.io"
GCP_PROJECT = "naap-orchestrator"
REGISTRY = "us-west1-docker.pkg.dev/naap-orchestrator/lpo-tool"
ORCHESTRATOR_ADDRESS = "0x2cC8AeC211Fdc372c0F7E4d1add1675733381328"
PRICE_USD = "0.015"  # hyperframes-render only: it has no catalogue price and no fleet competitor
# Owner decision 2026-11-05 (simple-infra#570 review point 4, option a): the same network price per
# tool as the fleet's paid tool hosts, so equally good hosts share traffic at no extra cost.
PRICES_COMMIT = "cb13dbf"

# Images: tool-ffmpeg is the agent-fleet staging pin, copied with the same digest; tool-app is
# the fleet's staging tool-app digest plus one file (../../images/tool-app-priced).
IMAGES = {
    "tool-app": f"{REGISTRY}/tool-app-priced@sha256:22ebeee81dbfb57cfe01fb9c855aaf8a8b99618d2059ade8b8ecaab428f29561",
    # tool-ffmpeg = agent-fleet prod pin (release 2026.11.16-1945Z, simple-infra 1981c89a), copied with
    # the same digest (LPA-516). Was 38b02a95 (2026-11-05). Only the worker changes: uploads stay in tool-app.
    "tool-ffmpeg": f"{REGISTRY}/tool-ffmpeg@sha256:885289bf2ba47fcb4a8ea7e4fe87f55c3a79c895128f539b82cdd83e68053ded",
    "tool-hyperframes": f"{REGISTRY}/tool-hyperframes@sha256:c716690c18d9c159ca9f53f1d965af7e7168c4a7eb21df7a7cc31df2567219d2",
}

# Tickets. EV 2e11 wei (about $0.0005), the fleet's tool ticket (fleet_hosts.py
# TOOL_TICKET_EXPECTED_VALUE_WEI): at or under the cheapest tool ($0.000476), so a call never pays a
# whole ticket more than its price. The earlier 6e12 (about $0.015) would charge every call $0.015
# whatever its price. TEST: face value 3e13, so 1 ticket in 150 wins. STEADY: face value 4e14, the fleet's.
TICKET_EV_WEI = 2 * 10**11
TICKET_FACE_VALUE_WEI = {"test": 3 * 10**13, "steady": 4 * 10**14}
TICKET_MODE = "test"

HYPERFRAMES_ROW = {
    "name": "hyperframes-render",
    "container": "tool-hyperframes",
    "endpoint": "/run/hyperframes-render",
    "capacity": 2,
    "price_per_unit": 1,
    "description": "Hyperframes render",
    "unit_kind": "call",
    "display_unit": "call",
    "display_price_usd": float(PRICE_USD),
}


def _export(commit: str, dest: Path) -> Path:
    archive = subprocess.run(
        ["git", "-C", str(SIMPLE_INFRA), "archive", commit, "agent-fleet"],
        check=True, capture_output=True,
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "agent-fleet"


def network_prices() -> dict[str, float]:
    """Each ffmpeg tool's network price per call, from simple-infra at PRICES_COMMIT."""
    raw = subprocess.run(
        ["git", "-C", str(SIMPLE_INFRA), "show", f"{PRICES_COMMIT}:agent-fleet/capabilities/catalogue-tool-prices.json"],
        check=True, capture_output=True, text=True,
    ).stdout
    return {name: row["per_call_usd"] for name, row in json.loads(raw)["prices"].items()}


def capabilities(fleet: Path) -> list[dict]:
    rows = json.loads((fleet / "capabilities" / "tool-ffmpeg.json").read_text())
    prices = network_prices()
    missing = sorted({r["name"] for r in rows} - prices.keys())
    if missing:
        raise SystemExit(f"no catalogue price for {', '.join(missing)} at {PRICES_COMMIT}")
    for row in rows:
        # display_price_usd for the SDK's catalogue; price_usd is what the release tool-app
        # (simple-infra#570) registers. The current overlay image ignores it and reads
        # LR_TOOL_PRICES_JSON, so the bundle is right with either image.
        row["display_price_usd"] = row["price_usd"] = prices[row["name"]]
    return [*rows, {**HYPERFRAMES_ROW, "price_usd": float(PRICE_USD)}]


HOST_DIR = "/opt/lpo/host"


def boot_files(fleet: Path, host_json: str) -> dict[str, str]:
    """The fleet boot program's host setup (boot/fleet_boot.py), with /opt/fleet -> HOST_DIR:
    the metadata-server firewall (only tool-app may reach it, for the bucket token), the
    Docker drop-in that re-applies it, and the compose unit."""
    import importlib.util

    loader = importlib.util.spec_from_file_location("fleet_boot", fleet / "boot" / "fleet_boot.py")
    boot = importlib.util.module_from_spec(loader)
    sys.modules["fleet_boot"] = boot  # dataclasses look the module up while it loads
    try:
        loader.loader.exec_module(boot)
    finally:
        del sys.modules["fleet_boot"]

    def here(text: str) -> str:
        return text.replace(str(boot.FLEET_DIR), HOST_DIR).replace("agent-fleet compose stack", "lpo-tool-2cc8 compose stack")

    return {
        "metadata-firewall.sh": here(boot.firewall_script(boot.metadata_allow(host_json))),
        "docker-dropin.conf": here(boot.DOCKER_DROPIN),
        "lpo-tool.service": here(boot.FLEET_UNIT),
    }


def render() -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        fleet = _export(FLEET_COMMIT, Path(tmp))
        sys.path.insert(0, str(fleet))
        try:
            import yaml

            from render import _host_json, caddyfile, compose_yaml, config_toml
            from render.classes import CADDY_IMAGE, NODE_IMAGE, TOOL, ToolContainer
            from render.spec import (
                WIN_PROB_DENOMINATOR, HostSpec, PaymentSettings, selected_class, validate, validate_payments,
            )

            # Difference 1.
            tool = dataclasses.replace(
                TOOL,
                tool_containers=(
                    *TOOL.tool_containers,
                    ToolContainer("tool-hyperframes", "hyperframes", cpus=2.0, memory="2g", pids=1024),
                ),
            )
            face = TICKET_FACE_VALUE_WEI[TICKET_MODE]
            payments = PaymentSettings(
                network="arbitrum-one-mainnet",
                orchestrator_address=ORCHESTRATOR_ADDRESS,
                ticket_face_value=str(face),
                ticket_win_prob=str(WIN_PROB_DENOMINATOR * TICKET_EV_WEI // face),
            )
            spec = HostSpec(
                host_class="tool",
                domain=DOMAIN,
                images={"orchestrator": NODE_IMAGE, "caddy": CADDY_IMAGE, **IMAGES},
                gcp_project=GCP_PROJECT,
                capabilities_json=json.dumps(capabilities(fleet), separators=(",", ":")),
                tool_containers=("tool-ffmpeg", "tool-hyperframes"),
                compose_profiles=("media", "hyperframes"),
                payments=payments,
            )
            host_class = selected_class(spec, tool)
            # Difference 2: every check of the fleet, payments included, except the tool-host rule.
            validate(dataclasses.replace(spec, payments=None), host_class)
            validate_payments(spec, dataclasses.replace(host_class, capabilities_port=None))

            compose = yaml.safe_load(compose_yaml(spec, host_class))
            # Difference 3.
            env = compose["services"]["tool-app"]["environment"]
            env["LR_TOOL_PRICE_USD"] = PRICE_USD
            env["LR_TOOL_PRICES_JSON"] = json.dumps(network_prices(), sort_keys=True, separators=(",", ":"))
            host_json = _host_json(spec, host_class)
            return {
                "config.toml": config_toml(spec, host_class),
                "Caddyfile": caddyfile(spec, host_class),
                "compose.yaml": yaml.safe_dump(compose, sort_keys=False, width=10_000),
                "host.json": host_json,
                "capabilities.json": json.dumps(capabilities(fleet), indent=2) + "\n",
                **boot_files(fleet, host_json),
            }
        finally:
            sys.path.remove(str(fleet))
            for name in [m for m in sys.modules if m == "render" or m.startswith("render.")]:
                del sys.modules[name]


def main(argv: list[str]) -> int:
    files = render()
    if "--check" in argv:
        stale = [n for n, body in files.items() if (HERE / n).read_text() != body]
        if stale:
            print(f"out of date: {', '.join(stale)}; run render_bundle.py", file=sys.stderr)
            return 1
        return 0
    for name, body in files.items():
        (HERE / name).write_text(body)
    print("wrote", ", ".join(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
