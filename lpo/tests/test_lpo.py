import json
from pathlib import Path

import pytest

from lpo import cli
from lpo.keccak import keccak256

ADDR = "0x1111111111111111111111111111111111111111"
LPO_YAML = f"""
orchestrator: {{host: node.example.org, eth_address: "{ADDR}"}}
capabilities:
  - {{app: community/hyperframes, mode: single-shot, price_info: {{price: 0.008, currency: usd, unit: fixed}}, capacity: 2}}
"""
DISCOVERY = json.dumps({"apps": [{"app": "community/hyperframes", "mode": "single-shot",
                                  "url": "https://node.example.org:8935/apps/r1/app", "price_info": {"price": 0.008, "unit": "fixed"}}]})
PROBE_OK = json.dumps({"ok": True, "elapsed_ms": 4100, "stream": {"codec_name": "h264", "width": 1920, "height": 1080, "nb_read_frames": "60"}})


class FakeEnv(cli.Env):
    def __init__(self, workdir, *, discovery=DISCOVERY, active=True, running=True, ips=("34.1.2.3",), probe=PROBE_OK):
        super().__init__(workdir)
        self.discovery, self.active, self.running, self.ips, self.probe = discovery, active, running, ips, probe

    def run(self, *args, input=None, timeout=60):
        if args[:3] == ("docker", "compose", "version"):
            return 0, "Docker Compose version v2.29.0"
        if args[:3] == ("docker", "compose", "ps"):
            state = "running" if self.running else "exited"
            return 0, f"orchestrator {state}\ncaddy running\nhyperframes running\n"
        if args[:3] == ("docker", "compose", "exec"):
            return 0, self.probe
        return 127, "unexpected"

    def get(self, url, timeout=5):
        return 200, self.discovery

    def resolve(self, host):
        return list(self.ips)

    def free_gib(self):
        return 80.0

    def eth_call(self, rpc, to, data):
        if data.startswith(cli._selector("getContract(bytes32)")):
            return "0x" + "0" * 24 + "35bcf3c30594191d53231e4ff333e8a770453e40"
        return "0x" + ("1" if self.active else "0").rjust(64, "0")


@pytest.fixture
def node(tmp_path):
    (tmp_path / "lpo.yaml").write_text(LPO_YAML)
    cli.render_kit(tmp_path, cli.load_config(tmp_path))
    sd = tmp_path / "secrets"
    (sd / "wallet-keystore").write_text(json.dumps({"address": ADDR[2:]}))
    (sd / "wallet-password").write_text("pw")
    (sd / "arbitrum-rpc-url").write_text("https://arb-mainnet.g.alchemy.com/v2/KEY")
    for f in ("wallet-keystore", "wallet-password", "arbitrum-rpc-url"):
        (sd / f).chmod(0o600)
    return tmp_path


def latest_report(workdir: Path) -> tuple[str, dict]:
    d = sorted((workdir / "lpo-report").iterdir())[-1]
    return (d / "report.md").read_text(), json.loads((d / "report.json").read_text())


def test_keccak_known_vectors():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert cli._selector("transfer(address,uint256)") == "0xa9059cbb"


def test_every_check_id_has_copy_and_a_known_stage():
    for cid, v in cli.VOCAB["checks"].items():
        assert v["stage"] in cli.VOCAB["stages"] and v["bar"] and v["why"] and v["fix"], cid


def test_render_writes_the_kit_and_never_the_wallet(tmp_path):
    (tmp_path / "lpo.yaml").write_text(LPO_YAML)
    written = cli.render_kit(tmp_path, cli.load_config(tmp_path))
    assert "compose.yaml" in written
    toml = (tmp_path / "config.toml").read_text()
    assert 'ServiceURL = "https://node.example.org"' in toml and ADDR in toml
    assert (tmp_path / "runners/hyperframes/app.py").exists()
    assert not (tmp_path / "secrets/wallet-keystore").exists()
    assert (tmp_path / "secrets/runner-bootstrap").stat().st_mode & 0o077 == 0


def test_ready_node_exits_zero_with_a_clean_report(node, capsys):
    assert cli.cmd_doctor(node, FakeEnv(node)) == 0
    md, js = latest_report(node)
    assert js["stage"] == "published" and js["passed"] == js["total"] == 13
    assert "Ready to publish." in capsys.readouterr().out


def test_inactive_node_stops_at_chain_with_fix_steps(node):
    assert cli.cmd_doctor(node, FakeEnv(node, active=False)) == 1
    md, js = latest_report(node)
    assert js["stage"] == "chain"
    assert "## ✗ chain.active: not in the active set this round" in md
    for section in ("**Blocks:**", "**The bar:**", "**Measured:**", "**Why it matters:**", "**What to do**"):
        assert section in md
    assert "## For an AI agent" in md


def test_unlisted_runner_and_foreign_host_and_price_drift(node):
    foreign = json.dumps({"apps": [{"app": "community/hyperframes", "mode": "single-shot", "url": "https://other.example.net/apps/x",
                                     "price_info": {"price": 0.02}}]})
    cli.cmd_doctor(node, FakeEnv(node, discovery=foreign))
    _, js = latest_report(node)
    failed = {c["id"] for c in js["checks"] if not c["ok"]}
    assert failed == {"runner.host", "manifest.match"}
    cli.cmd_doctor(node, FakeEnv(node, discovery=json.dumps({"apps": []})))
    _, js = latest_report(node)
    assert js["stage"] == "runners" and "runner.listed" in {c["id"] for c in js["checks"] if not c["ok"]}


def test_private_address_and_failed_contract(node):
    cli.cmd_doctor(node, FakeEnv(node, ips=("10.0.0.4",), probe=json.dumps({"ok": False, "error": "HTTP Error 502"})))
    _, js = latest_report(node)
    failed = {c["id"]: c for c in js["checks"] if not c["ok"]}
    assert js["stage"] == "node" and "net.public_ip" in failed and failed["cap.contract"]["measured"] == "HTTP Error 502"


def test_keystore_mismatch_and_missing_files(node):
    (node / "secrets/wallet-keystore").write_text(json.dumps({"address": "22" * 20}))
    cli.cmd_doctor(node, FakeEnv(node))
    _, js = latest_report(node)
    ks = next(c for c in js["checks"] if c["id"] == "chain.keystore")
    assert not ks["ok"] and "differs from eth_address" in ks["measured"]
    (node / "secrets/wallet-password").unlink()
    cli.cmd_doctor(node, FakeEnv(node))
    _, js = latest_report(node)
    assert "secrets/wallet-password" in next(c for c in js["checks"] if c["id"] == "chain.keystore")["measured"]


def test_incomplete_config_stops_early_and_reports_what_to_fill(tmp_path):
    (tmp_path / "lpo.yaml").write_text("orchestrator: {host: ''}\ncapabilities: []\n")
    assert cli.cmd_doctor(tmp_path, FakeEnv(tmp_path)) == 1
    _, js = latest_report(tmp_path)
    cfg = next(c for c in js["checks"] if c["id"] == "node.config")
    assert "orchestrator.host is empty" in cfg["measured"] and js["total"] == 3


def test_reports_never_contain_secrets(node):
    cli.cmd_doctor(node, FakeEnv(node, active=False))
    d = sorted((node / "lpo-report").iterdir())[-1]
    text = (d / "report.md").read_text() + (d / "report.json").read_text()
    assert "KEY" not in text and "pw" not in text.split()
    assert cli.redact("token=abc https://arb-mainnet.g.alchemy.com/v2/KEY") == "token=[redacted] [redacted-rpc-url]"
