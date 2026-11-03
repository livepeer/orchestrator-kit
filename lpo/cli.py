"""lpo: get a Livepeer orchestrator from a bare machine to paid traffic.

    lpo up       render the Kit from lpo.yaml, start it, run doctor (re-run after every fix)
    lpo doctor   check readiness and write report.md + report.json
    lpo init     render the Kit only

Exit codes: 0 ready, 1 blocking failures, 2 warnings only, 3 lpo itself could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from string import Template

from .keccak import keccak256

KIT_ROOT = Path(__file__).resolve().parent.parent
VOCAB = json.loads((Path(__file__).parent / "vocabulary.json").read_text())
SECRET_FILES = ("wallet-keystore", "wallet-password", "arbitrum-rpc-url")
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
# Ticket parameters from the fleet's paid orchestrators (simple-infra agent-fleet golden config).
TICKET_FACE_VALUE = "400000000000000"
TICKET_WIN_PROB = "57896044618658097711785492504343953926634992332820282019728792003956564819"


@dataclass
class Result:
    id: str
    ok: bool
    measured: str
    evidence: list[str] = field(default_factory=list)
    warn: bool = False

    @property
    def stage(self) -> str:
        return VOCAB["checks"][self.id]["stage"]


# ---------------------------------------------------------------- environment (injectable for tests)

class Env:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def run(self, *args: str, input: str | None = None, timeout: float = 60) -> tuple[int, str]:
        try:
            p = subprocess.run(args, cwd=self.workdir, input=input, capture_output=True, text=True, timeout=timeout)
            return p.returncode, (p.stdout + p.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 127, str(exc)

    def get(self, url: str, timeout: float = 5) -> tuple[int, str]:
        req = urllib.request.Request(url, headers={"User-Agent": "lpo-doctor"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
                return resp.status, resp.read(1 << 20).decode(errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except Exception as exc:  # noqa: BLE001 - every failure is evidence
            return 0, f"{type(exc).__name__}: {exc}"

    def resolve(self, host: str) -> list[str]:
        try:
            return sorted({i[4][0] for i in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
        except OSError:
            return []

    def free_gib(self) -> float:
        root = "/var/lib/docker" if Path("/var/lib/docker").exists() else str(self.workdir)
        return shutil.disk_usage(root).free / (1 << 30)

    def eth_call(self, rpc_url: str, to: str, data: str) -> str | None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]}).encode()
        req = urllib.request.Request(rpc_url, data=body, headers={"Content-Type": "application/json", "User-Agent": "lpo-doctor"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read()).get("result")
        except Exception:  # noqa: BLE001
            return None


# ---------------------------------------------------------------- config and rendering

def load_config(workdir: Path) -> dict:
    import yaml  # PyYAML, the only dependency

    path = workdir / "lpo.yaml"
    if not path.exists():
        raise FileNotFoundError("lpo.yaml not found; copy templates/hyperframes/lpo.yaml.example to lpo.yaml")
    return yaml.safe_load(path.read_text()) or {}


def config_problems(cfg: dict) -> list[str]:
    problems = []
    orch = cfg.get("orchestrator") or {}
    if not orch.get("host"):
        problems.append("orchestrator.host is empty")
    if not ADDRESS.match(str(orch.get("eth_address", ""))) or int(str(orch.get("eth_address", "0x0")), 16) == 0:
        problems.append("orchestrator.eth_address is not a wallet address")
    caps = cfg.get("capabilities") or []
    if not caps:
        problems.append("capabilities is empty")
    for i, cap in enumerate(caps):
        price = (cap.get("price_info") or {})
        if not cap.get("app") or cap.get("mode") not in ("single-shot", "persistent"):
            problems.append(f"capabilities[{i}] needs app and mode (single-shot or persistent)")
        if price.get("unit") not in ("fixed", "hour") or not isinstance(price.get("price"), (int, float)):
            problems.append(f"capabilities[{i}].price_info needs price and unit (fixed or hour)")
    return problems


def render_kit(workdir: Path, cfg: dict, template: str = "hyperframes") -> list[str]:
    """Write compose.yaml, Caddyfile, config.toml, .env and the runner build context. Never touches the wallet files."""
    src = KIT_ROOT / "templates" / template
    orch, cap = cfg["orchestrator"], cfg["capabilities"][0]
    secrets_dir = workdir / "secrets"
    secrets_dir.mkdir(mode=0o700, exist_ok=True)
    bootstrap = secrets_dir / "runner-bootstrap"
    if not bootstrap.exists():
        bootstrap.write_text(secrets.token_hex(32))
        bootstrap.chmod(0o600)
    values = {
        "HOST": orch["host"], "ETH_ADDRESS": orch["eth_address"], "APP_NAME": cap["app"],
        "PRICE_USD": str(cap["price_info"]["price"]), "CAPACITY": str(cap.get("capacity", 1)),
        "TICKET_FACE_VALUE": TICKET_FACE_VALUE, "TICKET_WIN_PROB": TICKET_WIN_PROB,
    }
    written = []
    shutil.copy(src / "compose.yaml", workdir / "compose.yaml")
    shutil.copy(src / "Caddyfile", workdir / "Caddyfile")
    (workdir / "config.toml").write_text(Template((src / "config.toml.tmpl").read_text()).substitute(values))
    env = {**{k: values[k] for k in ("HOST", "APP_NAME", "PRICE_USD", "CAPACITY")}, "ORCH_SECRET": bootstrap.read_text().strip()}
    (workdir / ".env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    (workdir / ".env").chmod(0o600)
    runner_dst = workdir / "runners" / template
    runner_dst.mkdir(parents=True, exist_ok=True)
    for name in ("Dockerfile", "app.py", "composition.py"):
        shutil.copy(KIT_ROOT / "runners" / template / name, runner_dst / name)
    written += ["compose.yaml", "Caddyfile", "config.toml", ".env", f"runners/{template}/"]
    return written


# ---------------------------------------------------------------- checks (one function per check id)

def check_docker(env: Env, cfg: dict) -> Result:
    code, out = env.run("docker", "compose", "version")
    return Result("host.docker", code == 0, out.strip().splitlines()[0] if out.strip() else "docker not found")


def check_disk(env: Env, cfg: dict) -> Result:
    free = env.free_gib()
    need = VOCAB["policy"]["min_free_disk_gib"]
    return Result("host.disk", free >= need, f"{free:.1f} GiB free (need {need})")


def check_config(env: Env, cfg: dict) -> Result:
    problems = config_problems(cfg)
    missing = [f for f in ("compose.yaml", "config.toml", ".env") if not (env.workdir / f).exists()]
    if missing:
        problems.append(f"not rendered yet: {', '.join(missing)} (run `lpo up`)")
    return Result("node.config", not problems, "; ".join(problems) or "complete")


def check_running(env: Env, cfg: dict) -> Result:
    code, out = env.run("docker", "compose", "ps", "--format", "{{.Service}} {{.State}}")
    states = dict(line.split(" ", 1) for line in out.strip().splitlines() if " " in line) if code == 0 else {}
    want = ("orchestrator", "caddy", "hyperframes")
    down = [s for s in want if states.get(s) != "running"]
    return Result("node.running", not down, "all running" if not down else f"not running: {', '.join(down)}",
                  [f"docker compose ps: {states or out.strip()[:300]}"])


def check_public_ip(env: Env, cfg: dict) -> Result:
    host = cfg["orchestrator"]["host"]
    ips = env.resolve(host)
    bad = [i for i in ips if not ip_address(i).is_global]
    ok = bool(ips) and not bad
    return Result("net.public_ip", ok, f"{host} → {', '.join(ips) or 'does not resolve'}")


def check_tls(env: Env, cfg: dict) -> Result:
    status, body = env.get(f"https://{cfg['orchestrator']['host']}/discovery")
    ok = status != 0
    return Result("net.tls", ok, f"HTTP {status}" if ok else body[:200])


def check_keystore(env: Env, cfg: dict) -> Result:
    sd = env.workdir / "secrets"
    missing = [f for f in SECRET_FILES if not (sd / f).exists() or (sd / f).stat().st_size == 0]
    if missing:
        return Result("chain.keystore", False, f"missing: {', '.join('secrets/' + m for m in missing)}")
    loose = [f for f in SECRET_FILES if (sd / f).stat().st_mode & 0o077]
    try:
        addr = "0x" + json.loads((sd / "wallet-keystore").read_text())["address"].lower().removeprefix("0x")
    except (ValueError, KeyError):
        return Result("chain.keystore", False, "secrets/wallet-keystore is not a keystore JSON file")
    want = cfg["orchestrator"]["eth_address"].lower()
    if addr != want:
        return Result("chain.keystore", False, f"keystore address {addr} differs from eth_address {want}")
    return Result("chain.keystore", True, "present and matching" + (f"; tighten to mode 600: {', '.join(loose)}" if loose else ""), warn=bool(loose))


def _selector(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()[:8]


def check_active(env: Env, cfg: dict) -> Result:
    rpc_file = env.workdir / "secrets" / "arbitrum-rpc-url"
    if not rpc_file.exists():
        return Result("chain.active", False, "no RPC URL yet (see chain.keystore)")
    rpc = rpc_file.read_text().strip()
    controller = VOCAB["policy"]["arbitrum_controller"]
    name_hash = keccak256(b"BondingManager").hex()
    bm = env.eth_call(rpc, controller, _selector("getContract(bytes32)") + name_hash)
    if not bm or int(bm, 16) == 0:
        return Result("chain.active", False, "could not look up BondingManager through the RPC URL", [f"controller {controller}"])
    bm_addr = "0x" + bm[-40:]
    addr = cfg["orchestrator"]["eth_address"].lower().removeprefix("0x").rjust(64, "0")
    res = env.eth_call(rpc, bm_addr, _selector("isActiveTranscoder(address)") + addr)
    active = bool(res) and int(res, 16) == 1
    return Result("chain.active", active, "active this round" if active else "not in the active set this round", [f"BondingManager {bm_addr}"])


def check_service_uri(env: Env, cfg: dict) -> Result:
    """Warn when the on-chain service URI points elsewhere: fine for a second host, but say so."""
    rpc_file = env.workdir / "secrets" / "arbitrum-rpc-url"
    if not rpc_file.exists():
        return Result("chain.service_uri", False, "no RPC URL yet (see chain.keystore)", warn=True)
    rpc, controller = rpc_file.read_text().strip(), VOCAB["policy"]["arbitrum_controller"]
    sr = env.eth_call(rpc, controller, _selector("getContract(bytes32)") + keccak256(b"ServiceRegistry").hex())
    addr = cfg["orchestrator"]["eth_address"].lower().removeprefix("0x").rjust(64, "0")
    raw = env.eth_call(rpc, "0x" + sr[-40:], _selector("getServiceURI(address)") + addr) if sr else None
    try:
        b = bytes.fromhex(raw[2:])
        uri = b[64:64 + int.from_bytes(b[32:64], "big")].decode()
    except (TypeError, ValueError, IndexError):
        return Result("chain.service_uri", False, "could not read the on-chain service URI", warn=True)
    host = cfg["orchestrator"]["host"]
    same = uri.split("/")[2].split(":")[0] == host if "://" in uri else False
    return Result("chain.service_uri", True, f"on-chain service URI is {uri or '(empty)'}" + ("" if same else f", not https://{host}"), warn=not same)


def _discovery_apps(body: str) -> list[dict]:
    try:
        doc = json.loads(body)
    except ValueError:
        return []
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "app" in node and isinstance(node["app"], str):
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(doc)
    return found


def check_runners(env: Env, cfg: dict) -> list[Result]:
    host = cfg["orchestrator"]["host"]
    status, body = env.get(f"https://{host}/discovery")
    apps = _discovery_apps(body) if status == 200 else []
    excerpt = f"GET https://{host}/discovery → HTTP {status}: {body[:400]}"
    wanted = cfg["capabilities"]
    listed = {a["app"]: a for a in apps}
    missing = [c["app"] for c in wanted if c["app"] not in listed]
    results = [Result("runner.listed", not missing, "all listed" if not missing else f"not listed: {', '.join(missing)}", [excerpt])]
    foreign = [a.get("url", "") for a in apps if a.get("url") and not a["url"].split("/")[2].split(":")[0] == host]
    results.append(Result("runner.host", not foreign, "all on this host" if not foreign else f"other hosts: {', '.join(foreign)}"))
    drift = []
    for c in wanted:
        a = listed.get(c["app"])
        if a and a.get("mode") and a["mode"] != c["mode"]:
            drift.append(f"{c['app']} mode {a['mode']} ≠ {c['mode']}")
        price = (a or {}).get("price_info") or {}
        if a and price and float(price.get("price", -1)) != float(c["price_info"]["price"]):
            drift.append(f"{c['app']} price {price.get('price')} ≠ {c['price_info']['price']}")
    results.append(Result("manifest.match", not drift and not missing, "; ".join(drift) or ("matches" if not missing else "runner not listed yet")))
    ceilings = VOCAB["policy"]["max_price_usd"]
    over = [f"{c['app']} ${c['price_info']['price']}/{c['price_info']['unit']} > ${ceilings[c['price_info']['unit']]}"
            for c in wanted if c["price_info"]["price"] > ceilings.get(c["price_info"]["unit"], 0)]
    results.append(Result("price.ceiling", not over, "; ".join(over) or "within the ceiling"))
    return results


CONTRACT_PROBE = r"""
import json, subprocess, sys, urllib.request
body = json.dumps({"params": {"html": "<div style='position:absolute;top:40%;width:100%;text-align:center;font:700 96px sans-serif;color:#fff'>lpo doctor</div>", "aspect_ratio": "16:9", "duration": 2}}).encode()
req = urllib.request.Request("http://127.0.0.1:8993/community/hyperframes", data=body, headers={"Content-Type": "application/json"})
try:
    data = json.loads(urllib.request.urlopen(req, timeout=240).read())["data"]
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)})); sys.exit(0)
path = "/outputs/" + data["url"].rsplit("/", 1)[1]
probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=codec_name,width,height,nb_read_frames", "-of", "json", path], capture_output=True, text=True)
streams = json.loads(probe.stdout or "{}").get("streams", [])
print(json.dumps({"ok": bool(streams) and int(streams[0].get("nb_read_frames", 0)) > 0, "elapsed_ms": data.get("elapsed_ms"), "stream": streams[0] if streams else None}))
"""


def check_contract(env: Env, cfg: dict) -> Result:
    code, out = env.run("docker", "compose", "exec", "-T", "hyperframes", "python", "-", input=CONTRACT_PROBE, timeout=300)
    try:
        res = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Result("cap.contract", False, "the sample request did not run", [out.strip()[-600:]])
    measured = (f"MP4 {res['stream']['width']}x{res['stream']['height']}, {res['stream']['nb_read_frames']} frames, {res['elapsed_ms']} ms"
                if res.get("ok") else res.get("error") or "no video stream in the output")
    return Result("cap.contract", bool(res.get("ok")), measured, [json.dumps(res)[:600]])


def run_checks(env: Env, cfg: dict) -> list[Result]:
    results = [check_docker(env, cfg), check_disk(env, cfg), check_config(env, cfg)]
    if config_problems(cfg):
        return results
    results += [check_running(env, cfg), check_public_ip(env, cfg), check_tls(env, cfg), check_keystore(env, cfg), check_active(env, cfg), check_service_uri(env, cfg)]
    results += check_runners(env, cfg)
    results.append(check_contract(env, cfg) if results[3].ok else Result("cap.contract", False, "skipped: the stack is not running"))
    return results


# ---------------------------------------------------------------- report

REDACT = [re.compile(r"(?i)(secret|password|token|key)=\S+"), re.compile(r"https?://[^\s\"']*(alchemy|infura|quiknode)[^\s\"']*")]


def redact(text: str) -> str:
    for pattern in REDACT:
        text = pattern.sub(lambda m: m.group(0).split("=")[0] + "=[redacted]" if "=" in m.group(0) else "[redacted-rpc-url]", text)
    return text


def where(results: list[Result]) -> str:
    for stage in VOCAB["stages"]:
        if any(r.stage == stage and not r.ok and VOCAB["checks"][r.id]["blocking"] for r in results):
            return stage
    return "published"


def write_report(workdir: Path, cfg: dict, results: list[Result]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = workdir / "lpo-report" / stamp
    out.mkdir(parents=True, exist_ok=True)
    stage = where(results)
    blocking = [r for r in results if not r.ok and VOCAB["checks"][r.id]["blocking"]]
    warnings = [r for r in results if r.warn or (not r.ok and not VOCAB["checks"][r.id]["blocking"])]
    passed = sum(r.ok for r in results)
    host = (cfg.get("orchestrator") or {}).get("host", "?")
    lines = [
        f"# lpo doctor report · {host} · {stamp}",
        "",
        f"**Where you are:** stage {VOCAB['stages'].index(stage) + 1} of {len(VOCAB['stages'])}: `{stage}` "
        f"({' → '.join(VOCAB['stages'])}). **Readiness:** {passed}/{len(results)} checks pass, {len(blocking)} blocking.",
        "",
        "## For an AI agent helping with this node",
        "Goal: make every blocking check below pass, then run `lpo up` again. You may edit `lpo.yaml`, `compose.yaml` and "
        "`Caddyfile`, and run `lpo doctor`, `lpo up`, `docker compose ...`. Never print, copy or move the files in `secrets/`, "
        "and never change on-chain stake without the operator.",
        "",
    ]
    for r in blocking + [w for w in warnings if w not in blocking]:
        v = VOCAB["checks"][r.id]
        lines += [f"## {'✗' if not r.ok else '!'} {r.id}: {r.measured}", "",
                  f"**Blocks:** {'publishing (you stay at stage `' + r.stage + '`)' if v['blocking'] and not r.ok else 'nothing (warning)'}",
                  f"**The bar:** {v['bar']}", f"**Measured:** {r.measured}", f"**Why it matters:** {v['why']}", ""]
        if r.evidence:
            lines += ["**Evidence**"] + [f"- `{redact(e)}`" for e in r.evidence] + [""]
        lines += ["**What to do**"] + [f"{i}. {s}" for i, s in enumerate(v["fix"], 1)] + [""]
    lines += ["## All checks", "", "| Stage | Check | Result | Measured |", "|---|---|---|---|"]
    lines += [f"| {r.stage} | {r.id} | {'pass' if r.ok else 'FAIL'} | {redact(r.measured)} |" for r in results]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    (out / "report.json").write_text(json.dumps({"host": host, "at": stamp, "stage": stage, "passed": passed, "total": len(results),
                                                 "vocabulary_version": VOCAB["version"],
                                                 "checks": [{**asdict(r), "stage": r.stage, "measured": redact(r.measured),
                                                             "evidence": [redact(e) for e in r.evidence]} for r in results]}, indent=2))
    return out


def print_summary(results: list[Result], report_dir: Path) -> int:
    blocking = [r for r in results if not r.ok and VOCAB["checks"][r.id]["blocking"]]
    warns = [r for r in results if r.warn]
    for r in blocking:
        print(f"  ✗ {r.stage:<8} {r.id:<16} {r.measured}")
    for r in warns:
        print(f"  ! {r.stage:<8} {r.id:<16} {r.measured}")
    print(f"  ✓ {sum(r.ok for r in results)} checks pass")
    stage = where(results)
    print(f"\nStage {VOCAB['stages'].index(stage) + 1}/{len(VOCAB['stages'])}: {stage}. "
          + ("Ready to publish." if not blocking else f"Not ready: {len(blocking)} blocking."))
    if blocking:
        print("\nNext step\n  " + VOCAB["checks"][blocking[0].id]["fix"][0])
    print(f"\nFull report: {report_dir / 'report.md'} (and report.json)\nPaste report.md into your AI coding agent for step-by-step help.")
    return 1 if blocking else (2 if warns else 0)


# ---------------------------------------------------------------- commands

def cmd_doctor(workdir: Path, env: Env | None = None) -> int:
    env = env or Env(workdir)
    cfg = load_config(workdir)
    results = run_checks(env, cfg)
    return print_summary(results, write_report(workdir, cfg, results))


def cmd_init(workdir: Path) -> int:
    cfg = load_config(workdir)
    problems = config_problems(cfg)
    if problems:
        print("lpo.yaml needs: " + "; ".join(problems))
        return 1
    print("Rendered: " + ", ".join(render_kit(workdir, cfg)))
    return 0


def cmd_up(workdir: Path) -> int:
    if cmd_init(workdir) != 0:
        return 1
    missing = [f for f in SECRET_FILES if not (workdir / "secrets" / f).exists()]
    if missing:
        print("Waiting for your wallet files: " + ", ".join("secrets/" + m for m in missing) + " (mode 600). Then run `lpo up` again.")
    else:
        code, out = Env(workdir).run("docker", "compose", "up", "-d", "--build", timeout=1800)
        print(out.strip()[-1500:])
        if code != 0:
            return 3
    return cmd_doctor(workdir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lpo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["up", "doctor", "init"])
    parser.add_argument("--dir", default=os.getcwd(), help="node directory holding lpo.yaml (default: current)")
    args = parser.parse_args(argv)
    workdir = Path(args.dir).resolve()
    try:
        return {"up": cmd_up, "doctor": cmd_doctor, "init": cmd_init}[args.command](workdir)
    except FileNotFoundError as exc:
        print(str(exc))
        return 3


if __name__ == "__main__":
    sys.exit(main())
