"""Opt-in live VPS smoke test, without installing TUN or changing OS routes.

Uses an exported ncloud JSON/YAML profile. Credentials stay in memory/private
temporary files and are never printed. Requires a separately verified Mihomo.
"""

import argparse
import contextlib
import copy
import http.client
import json
from pathlib import Path
import secrets
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ncloud.config import GROUP, json_text
from ncloud.platforms import atomic_write, run
from verify_cores import free_port, process


def controller(port, secret, path):
    request = urllib.request.Request("http://127.0.0.1:{}{}".format(port, path),
                                     headers={"Authorization": "Bearer " + secret})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return json.load(response)


@contextlib.contextmanager
def connect(proxy_port, hostname):
    # Explicit CONNECT cannot be silently bypassed by Windows proxy exclusions.
    connection = http.client.HTTPSConnection("127.0.0.1", proxy_port, timeout=20,
                                            context=ssl.create_default_context())
    try:
        connection.set_tunnel(hostname, 443)
        connection.connect()
        yield connection
    finally:
        connection.close()


def request_json(connection, path):
    connection.request("GET", path, headers={"User-Agent": "ncloud-smoke/0.1"})
    response = connection.getresponse()
    body = response.read(256 * 1024)
    if response.status != 200:
        raise RuntimeError("origin HTTP " + str(response.status))
    return json.loads(body)


def route_for(controller_port, secret, host):
    for _ in range(10):
        connections = controller(controller_port, secret, "/connections")["connections"]
        matches = [value for value in connections if value.get("metadata", {}).get("host") == host]
        if matches:
            value = matches[-1]
            return {"chains": value.get("chains", []), "rule": value.get("rule"), "payload": value.get("rulePayload")}
        time.sleep(0.1)
    raise RuntimeError("live route was not visible for " + host)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="private ncloud-exported JSON/YAML file")
    parser.add_argument("--mihomo", required=True)
    args = parser.parse_args()
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    expected_ip = profile["proxies"][0]["server"]
    mihomo = Path(args.mihomo).resolve()
    controller_port, mixed_port, dns_port = free_port(), free_port(), free_port()
    secret = secrets.token_hex(24)
    runtime = copy.deepcopy(profile)
    runtime["tun"]["enable"] = False
    runtime["mixed-port"] = mixed_port
    runtime["dns"]["listen"] = "127.0.0.1:" + str(dns_port)
    runtime["external-controller"] = "127.0.0.1:" + str(controller_port)
    runtime["secret"] = secret
    # Keep the full exported DNS policy, rule providers and routing rules.
    with tempfile.TemporaryDirectory(prefix="ncloud-vps-check-") as temporary:
        directory = Path(temporary)
        path = directory / "runtime.yaml"
        atomic_write(path, json_text(runtime))
        run([mihomo, "-t", "-d", directory, "-f", path], timeout=120)
        with process([mihomo, "-d", directory, "-f", path], directory, "mihomo-vps", mixed_port):
            deadline = time.monotonic() + 70
            while True:
                providers = controller(controller_port, secret, "/providers/rules")["providers"]
                if all(providers.get(name, {}).get("ruleCount", 0) > 0 for name in ("cn-domain", "cn-ip")):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("China rule providers did not finish loading through the VPS")
                time.sleep(1)
            print("PASS: CN domain/IP rules downloaded through the Japan node")
            with connect(mixed_port, "api.ipify.org") as connection:
                route = route_for(controller_port, secret, "api.ipify.org")
                if GROUP not in route["chains"] or "DIRECT" in route["chains"]:
                    raise RuntimeError("foreign IP check did not use the Japan node")
                ip = request_json(connection, "/?format=json")["ip"]
                if ip != expected_ip:
                    raise RuntimeError("proxy egress differs from the VPS address")
                print("PASS: proxied HTTPS egress matches the VPS IPv4 " + ip)
            with connect(mixed_port, "www.baidu.com") as connection:
                route = route_for(controller_port, secret, "www.baidu.com")
                if route["chains"] != ["DIRECT"] or route["rule"] != "RuleSet":
                    raise RuntimeError("China domain was not routed DIRECT by the CN rule set")
                print("PASS: www.baidu.com TLS connection uses CN-domain DIRECT rule")
            dns = controller(controller_port, secret, "/dns/query?name=www.example.org&type=A")
            if dns.get("Status") != 0 or not dns.get("Answer"):
                raise RuntimeError("foreign DNS resolution through the configured policy failed")
            print("PASS: foreign DNS query succeeded with the exported DoH policy")
            country = None
            for host, path, key in (("ipinfo.io", "/" + expected_ip + "/json", "country"),
                                    ("ipwho.is", "/" + expected_ip, "country_code")):
                try:
                    with connect(mixed_port, host) as connection:
                        country = request_json(connection, path).get(key)
                    if country:
                        break
                except (OSError, RuntimeError, ValueError, http.client.HTTPException):
                    pass
            if country != "JP":
                raise RuntimeError("Japan geolocation could not be verified; reported country: " + str(country))
            print("PASS: external IP geolocation reports JP")
    print("Not tested: Windows TUN interception, browser-specific DoH and OS IPv6 leak behavior.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
