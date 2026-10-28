"""Pure configuration construction, shared by the CLI and tests."""

import base64
import ipaddress
import json
import re
import uuid

FLOW = "xtls-rprx-vision"
NODE = "日本 VPS"
GROUP = "日本节点"
PRIVATE_NETWORKS = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4",
    "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8",
]


class NcloudError(Exception):
    pass


def public_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError):
        raise NcloudError("--server must be the VPS public IPv4 address")
    if not address.is_global or address.is_multicast:
        raise NcloudError("--server must be a globally routable unicast IPv4 address")
    return str(address)


def port_number(value):
    if isinstance(value, bool):
        raise NcloudError("port must be an integer from 1 to 65535")
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise NcloudError("port must be an integer from 1 to 65535")
    if str(number) != str(value) or not 1 <= number <= 65535:
        raise NcloudError("port must be an integer from 1 to 65535")
    return number


def hostname(value):
    if not isinstance(value, str) or len(value) > 253:
        raise NcloudError("server name must be a DNS hostname")
    labels = value.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label) for label in labels):
        raise NcloudError("server name must be a DNS hostname without a URL, port or path")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value.lower()
    raise NcloudError("REALITY server name must be a hostname, not an IP address")


def key32(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise NcloudError("invalid REALITY key")
    try:
        raw = base64.urlsafe_b64decode(value + "=")
    except (ValueError, TypeError):
        raise NcloudError("invalid REALITY key")
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
        raise NcloudError("invalid REALITY key")
    return value


def validate_state(state):
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise NcloudError("unsupported or invalid state file")
    required = ("server", "port", "server_name", "uuid", "private_key", "public_key", "short_id", "xray_version", "binary_sha256")
    if any(key not in state for key in required):
        raise NcloudError("state file is incomplete")
    public_ipv4(state["server"])
    port_number(state["port"])
    hostname(state["server_name"])
    try:
        if str(uuid.UUID(state["uuid"])) != state["uuid"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise NcloudError("invalid UUID in state")
    key32(state["private_key"])
    key32(state["public_key"])
    if not isinstance(state["short_id"], str) or not re.fullmatch(r"[0-9a-f]{16}", state["short_id"]):
        raise NcloudError("short ID must contain 16 lowercase hexadecimal characters")
    if not isinstance(state["binary_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", state["binary_sha256"]):
        raise NcloudError("invalid binary checksum in state")
    if not isinstance(state["xray_version"], str) or not re.fullmatch(r"\d+\.\d+\.\d+", state["xray_version"]):
        raise NcloudError("invalid Xray version in state")
    return state


def server_config(state):
    validate_state(state)
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "tag": "reality-in", "listen": "0.0.0.0", "port": state["port"],
            "protocol": "vless",
            "settings": {"clients": [{"id": state["uuid"], "flow": FLOW}], "decryption": "none"},
            "streamSettings": {
                "network": "tcp", "security": "reality",
                "realitySettings": {
                    "show": False, "target": state["server_name"] + ":443",
                    "serverNames": [state["server_name"]],
                    "privateKey": state["private_key"], "shortIds": [state["short_id"]],
                },
            },
        }],
        "outbounds": [{"protocol": "freedom", "tag": "direct"}, {"protocol": "blackhole", "tag": "block"}],
        "routing": {"domainStrategy": "IPOnDemand", "rules": [
            {"type": "field", "ip": PRIVATE_NETWORKS, "outboundTag": "block"},
        ]},
    }


def client_config(state):
    validate_state(state)
    # JSON is a YAML 1.2 subset. No PyYAML dependency is necessary.
    base = "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/"
    providers = {}
    for name, behavior, suffix in (("cn-domain", "domain", "geosite/cn.mrs"), ("cn-ip", "ipcidr", "geoip/cn.mrs")):
        providers[name] = {
            "type": "http", "behavior": behavior, "format": "mrs", "interval": 86400,
            "path": "./ruleset/" + name + ".mrs", "url": base + suffix, "proxy": GROUP,
        }
    return {
        "mixed-port": 7897, "allow-lan": False, "bind-address": "127.0.0.1",
        "mode": "rule", "log-level": "info", "ipv6": True,
        "find-process-mode": "strict",
        "profile": {"store-selected": True},
        "tun": {"enable": True, "stack": "mixed", "auto-route": True,
                "strict-route": True, "auto-detect-interface": True,
                "dns-hijack": ["any:53", "tcp://any:53"]},
        "dns": {
            "enable": True, "listen": "127.0.0.1:1053", "ipv6": True,
            "use-system-hosts": False,
            "enhanced-mode": "fake-ip", "fake-ip-range": "198.18.0.1/16",
            "fake-ip-filter": ["*.lan", "*.local", "localhost", "+.home.arpa"],
            "default-nameserver": ["223.5.5.5", "119.29.29.29"],
            "proxy-server-nameserver": ["https://dns.alidns.com/dns-query"],
            "direct-nameserver": ["https://dns.alidns.com/dns-query"],
            "nameserver": ["https://cloudflare-dns.com/dns-query#" + GROUP],
            "nameserver-policy": {"rule-set:cn-domain": ["https://dns.alidns.com/dns-query"]},
        },
        "proxies": [{
            "name": NODE, "type": "vless", "server": state["server"], "port": state["port"],
            "uuid": state["uuid"], "network": "tcp", "udp": True, "tls": True,
            "flow": FLOW, "servername": state["server_name"], "client-fingerprint": "chrome",
            "packet-encoding": "xudp", "reality-opts": {
                "public-key": state["public_key"], "short-id": state["short_id"],
                "support-x25519mlkem768": True,
            },
        }],
        "proxy-groups": [{"name": GROUP, "type": "select", "proxies": [NODE]}],
        "rule-providers": providers,
        "rules": [
            "DOMAIN,localhost,DIRECT", "DOMAIN-SUFFIX,lan,DIRECT", "DOMAIN-SUFFIX,local,DIRECT",
            "DOMAIN-SUFFIX,home.arpa,DIRECT",
        ] + ["IP-CIDR" + ("6" if ":" in network else "") + "," + network + ",DIRECT,no-resolve" for network in PRIVATE_NETWORKS]
          + ["RULE-SET,cn-domain,DIRECT", "RULE-SET,cn-ip,DIRECT,no-resolve", "MATCH," + GROUP],
    }


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
