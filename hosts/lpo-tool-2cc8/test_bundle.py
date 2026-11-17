"""lpo-tool-2cc8 host files: the fleet renderer's output plus the three listed differences only."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

import render_bundle as rb

HERE = Path(__file__).resolve().parent
AGENT_REGISTRY = HERE.parents[2] / "livepeer-agent-main" / "lib" / "capabilities" / "registry.json"
WIN_PROB_DENOMINATOR = 2**256
SIGNER_MAX_TICKET_EV_WEI = 3 * 10**13  # agent-fleet render/signer.py MaxTicketEV


@pytest.mark.skipif(not (rb.SIMPLE_INFRA / ".git").exists(), reason="needs a simple-infra checkout next to the Kit")
def test_committed_files_are_the_renderer_output():
    assert rb.main(["--check"]) == 0


def test_each_tool_is_priced_at_the_fleet_network_price():
    compose = yaml.safe_load((HERE / "compose.yaml").read_text())
    env = compose["services"]["tool-app"]["environment"]
    caps = json.loads(env["CAPABILITIES_JSON"])
    assert caps == json.loads((HERE / "capabilities.json").read_text())
    prices = json.loads(env["LR_TOOL_PRICES_JSON"])
    # Every ffmpeg tool has its own price, shown and registered alike; only hyperframes-render
    # (no catalogue price) falls back to the flat rate.
    assert {c["name"] for c in caps} - set(prices) == {"hyperframes-render"}
    assert env["LR_TOOL_PRICE_USD"] == "0.015"
    for c in caps:
        assert c["display_price_usd"] == c["price_usd"] == prices.get(c["name"], 0.015), c["name"]
    assert prices["ffmpeg-trim"] == 0.002381 and prices["ffprobe-info"] == 0.000476
    assert all(p > 0 for p in prices.values())  # a paid Node refuses a free runner


def test_one_ticket_is_worth_no_more_than_the_cheapest_tool():
    config = tomllib.loads((HERE / "config.toml").read_text())
    ev = int(config["TicketFaceValue"]) * int(config["TicketWinProb"]) // WIN_PROB_DENOMINATOR
    cheapest = min(json.loads(yaml.safe_load((HERE / "compose.yaml").read_text())["services"]["tool-app"]["environment"]["LR_TOOL_PRICES_JSON"]).values())
    # 2e11 wei is under the cheapest tool ($0.000476) at any ETH price up to about $2,380.
    assert ev * 2380 / 10**18 <= cheapest, ev


@pytest.mark.skipif(not AGENT_REGISTRY.exists(), reason="needs a livepeer-agent checkout next to the Kit")
def test_every_capability_name_is_one_the_agent_knows():
    known = {row["name"] for row in json.loads(AGENT_REGISTRY.read_text())}
    names = [c["name"] for c in json.loads((HERE / "capabilities.json").read_text())]
    assert len(names) == 17 and set(names) <= known, set(names) - known


def test_node_pays_out_to_0x2cc8_within_the_signer_cap():
    config = tomllib.loads((HERE / "config.toml").read_text())
    assert config["Network"] == "arbitrum-one-mainnet"
    assert config["OrchestratorAddress"] == "0x2cC8AeC211Fdc372c0F7E4d1add1675733381328"
    ev = int(config["TicketFaceValue"]) * int(config["TicketWinProb"]) // WIN_PROB_DENOMINATOR
    assert 0 < ev <= SIGNER_MAX_TICKET_EV_WEI


def test_no_image_is_unpinned():
    compose = yaml.safe_load((HERE / "compose.yaml").read_text())
    for name, service in compose["services"].items():
        assert "@sha256:" in service["image"] and "HYPERFRAMES_DIGEST" not in service["image"], name


def test_output_bucket_keeps_results_at_least_as_long_as_the_fleet():
    # LPA-527: a hand-set 2-day rule deleted users' results before the 7-day window the Beta Terms
    # promise. The rule lives here now, and matches the fleet tool-output bucket (30 days).
    rules = json.loads((HERE / "lifecycle.json").read_text())["rule"]
    deletes = [r for r in rules if r["action"]["type"] == "Delete"]
    assert deletes and all(set(r["condition"]) == {"age"} for r in deletes), rules
    assert min(r["condition"]["age"] for r in deletes) >= 30, rules
