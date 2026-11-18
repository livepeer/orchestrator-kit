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
    def __init__(self, workdir, *, discovery=DISCOVERY, active=True, running=True, ips=("34.1.2.3",), probe=PROBE_OK, service_uri="https://node.example.org"):
        super().__init__(workdir)
        self.service_uri = service_uri
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
        if data.startswith(cli._selector("getServiceURI(address)")):
            uri = self.service_uri.encode()
            return "0x" + (32).to_bytes(32, "big").hex() + len(uri).to_bytes(32, "big").hex() + uri.ljust(32 * ((len(uri) + 31) // 32), b"\0").hex()
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
    assert js["stage"] == "published" and js["passed"] == js["total"] == 14
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


def test_service_uri_elsewhere_is_a_warning_not_a_block(node, capsys):
    assert cli.cmd_doctor(node, FakeEnv(node, service_uri="https://livebit.ca:8935")) == 2
    md, js = latest_report(node)
    su = next(c for c in js["checks"] if c["id"] == "chain.service_uri")
    assert su["ok"] and su["warn"] and "livebit.ca" in su["measured"] and js["stage"] == "published"
    assert "## ! chain.service_uri" in md


NEW = "0x2222222222222222222222222222222222222222"


class ChainEnv(FakeEnv):
    """Fakes `docker run ... livepeer-chain` and a full 3-seat pool whose lowest stake is 610.2 LPT."""

    def __init__(self, workdir, **kw):
        super().__init__(workdir, **kw)
        self.calls = []

    def run(self, *args, input=None, timeout=60):
        if args[:2] == ("docker", "run"):
            self.calls.append(args)
            if "create" in args:
                (self.workdir / "secrets" / "wallet-keystore").write_text(json.dumps({"address": NEW[2:], "crypto": {}}))
            return 0, "simulated: approve, bond, transcoder, setServiceURI" if "register" in args else "created"
        return super().run(*args, input=input, timeout=timeout)

    def eth_call(self, rpc, to, data):
        pool = ["0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40]
        stakes = {pool[0][2:]: 9000, pool[1][2:]: 2000, pool[2][2:]: 610.2}
        word = lambda n: "0x" + int(n).to_bytes(32, "big").hex()
        if data.startswith(cli._selector("getTranscoderPoolSize()")) or data.startswith(cli._selector("getTranscoderPoolMaxSize()")):
            return word(3)
        if data.startswith(cli._selector("getFirstTranscoderInPool()")):
            return "0x" + pool[0][2:].rjust(64, "0")
        if data.startswith(cli._selector("getNextTranscoderInPool(address)")):
            i = [p[2:] for p in pool].index(data[-40:])
            return "0x" + (pool[i + 1][2:] if i + 1 < len(pool) else "0" * 40).rjust(64, "0")
        if data.startswith(cli._selector("transcoderTotalStake(address)")):
            return word(int(stakes[data[-40:]] * 10**18))
        return super().eth_call(rpc, to, data)


def answers(*values):
    it = iter(values)
    return lambda prompt: next(it)


def test_wallet_creates_private_files_and_fills_the_address(tmp_path, capsys):
    (tmp_path / "lpo.yaml").write_text(LPO_YAML.replace(ADDR, "0x" + "0" * 40))
    env = ChainEnv(tmp_path)
    assert cli.cmd_wallet(tmp_path, env, ask=answers("correct horse battery", "correct horse battery", "https://arb1.example/rpc")) == 0
    sd = tmp_path / "secrets"
    for f in cli.SECRET_FILES:
        assert (sd / f).stat().st_mode & 0o077 == 0, f
    assert cli.load_config(tmp_path)["orchestrator"]["eth_address"] == NEW
    out = capsys.readouterr().out
    assert NEW in out and "more than 610.2 LPT" in out and "correct horse" not in out
    assert any(a.endswith("/s") for a in env.calls[0])  # writable mount only for create


def test_wallet_never_replaces_one_and_rejects_weak_passwords(node, tmp_path):
    assert cli.cmd_wallet(node, ChainEnv(node)) == 1
    (tmp_path / "w").mkdir()
    (tmp_path / "w" / "lpo.yaml").write_text(LPO_YAML)
    assert cli.cmd_wallet(tmp_path / "w", ChainEnv(tmp_path / "w"), ask=answers("short", "short")) == 1
    assert not (tmp_path / "w" / "secrets" / "wallet-password").exists()


def test_register_is_a_dry_run_unless_submit_and_checks_the_stake(node, capsys):
    env = ChainEnv(node)
    assert cli.cmd_register(node, "500", False, "100", "100", env) == 1 and not env.calls
    assert "bond more than 610.2" in capsys.readouterr().out
    assert cli.cmd_register(node, "800", False, "100", "100", env) == 0
    args = env.calls[-1]
    assert "--submit" not in args and "https://node.example.org" in args and args[args.index("-v") + 1].endswith(":ro")
    assert "Dry run only" in capsys.readouterr().out
    assert cli.cmd_register(node, "800", True, "100", "100", env) == 0 and "--submit" in env.calls[-1]


def test_rpc_url_file_with_a_newline_is_caught(node):
    (node / "secrets" / "arbitrum-rpc-url").write_text("https://arb1.example/rpc\n")
    r = cli.check_keystore(cli.Env(node), cli.load_config(node))
    assert not r.ok and "newline" in r.measured
