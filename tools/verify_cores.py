"""Opt-in validation against real, separately downloaded upstream binaries.

Never changes OS routes or installs services. Real traffic stays on loopback
until Xray makes the outbound connection. Runtime tests deliberately force
MATCH to the proxy so a direct fallback cannot produce a false positive.
"""

import argparse
import contextlib
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ncloud.config import client_config, json_text, server_config
from ncloud.platforms import atomic_write, generate_keys, run, sha256_file, target_check
from ncloud.releases import DEFAULT_VERSION


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextlib.contextmanager
def process(args, directory, label, port):
    log_path = directory / (label + ".log")
    with log_path.open("wb") as log:
        child = subprocess.Popen([str(value) for value in args], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(label + " exited: " + log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.15)
            else:
                raise RuntimeError(label + " did not open its listener")
            yield child
        except Exception:
            print(label + " diagnostic log:\n" + log_path.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
            raise
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def fetch_through(port, url):
    proxy = "http://127.0.0.1:" + str(port)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    try:
        response = opener.open(url, timeout=12)
    except urllib.error.HTTPError as error:
        # An authenticated HTTPS origin response (including a WAF 403) proves
        # transport. A CONNECT/proxy failure instead raises URLError/OSError.
        response = error
    with response:
        return response.status, response.read(65536)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xray", required=True)
    parser.add_argument("--mihomo", required=True)
    parser.add_argument("--network", action="store_true", help="allow TLS probe, rule downloads and live loopback relay test")
    parser.add_argument("--url", default="https://www.cloudflare.com/robots.txt")
    parser.add_argument("--server-name", default="www.cloudflare.com")
    args = parser.parse_args()
    xray = Path(args.xray).resolve()
    mihomo = Path(args.mihomo).resolve()
    private, public = generate_keys(xray)
    state = {"schema_version": 1, "server": "8.8.8.8", "port": free_port(),
             "server_name": args.server_name, "uuid": str(uuid.uuid4()),
             "private_key": private, "public_key": public, "short_id": os.urandom(8).hex(),
             "xray_version": DEFAULT_VERSION, "binary_sha256": sha256_file(xray)}
    # No credentials survive outside this private temporary directory.
    with tempfile.TemporaryDirectory(prefix="ncloud-core-check-") as temporary:
        directory = Path(temporary)
        configuration = server_config(state)
        configuration["inbounds"][0]["listen"] = "127.0.0.1"
        server_path = directory / "server.json"
        atomic_write(server_path, json_text(configuration))
        run([xray, "run", "-test", "-config", server_path])
        print("PASS: generated server configuration accepted by Xray")
        if not args.network:
            print("Network checks skipped; rerun with --network to validate Mihomo rules and relay traffic.")
            return 0
        target_check(state["server_name"])
        profile = client_config(state)
        profile_path = directory / "client.yaml"
        atomic_write(profile_path, json_text(profile))
        result = run([mihomo, "-t", "-d", directory, "-f", profile_path], timeout=120)
        print("PASS: complete generated client profile accepted by Mihomo")
        runtime = copy.deepcopy(profile)
        runtime["tun"]["enable"] = False
        runtime["dns"] = {"enable": False}
        runtime["proxies"][0]["server"] = "127.0.0.1"
        runtime["rules"] = ["MATCH,日本节点"]
        runtime["rule-providers"] = {}
        runtime["mixed-port"] = free_port()
        runtime_path = directory / "runtime.yaml"
        atomic_write(runtime_path, json_text(runtime))
        with process([xray, "run", "-config", server_path], directory, "xray", state["port"]):
            with process([mihomo, "-d", directory, "-f", runtime_path], directory, "mihomo", runtime["mixed-port"]):
                status, body = fetch_through(runtime["mixed-port"], args.url)
                if not body:
                    raise RuntimeError("empty HTTPS response")
            print("PASS: HTTPS through Mihomo -> VLESS / REALITY / Vision -> Xray (origin HTTP {})".format(status))
            runtime["proxies"][0]["uuid"] = str(uuid.uuid4())
            runtime["mixed-port"] = free_port()
            atomic_write(runtime_path, json_text(runtime))
            with process([mihomo, "-d", directory, "-f", runtime_path], directory, "mihomo-bad-auth", runtime["mixed-port"]):
                try:
                    fetch_through(runtime["mixed-port"], args.url)
                except (OSError, urllib.error.URLError):
                    print("PASS: invalid UUID rejected; no DIRECT fallback")
                else:
                    raise RuntimeError("invalid credentials unexpectedly relayed traffic")
        print("Note: loopback tests do not verify VPS deployment, TUN or China/Japan routing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
