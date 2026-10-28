"""Platform operations, deliberately separated from pure config generation."""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import urllib.request
import zipfile

from .config import NcloudError, key32
from .releases import RELEASES

SERVICE = "ncloud-xray.service"
UNIT_MARKER = "# Managed by ncloud"


def run(args, check=True, timeout=45):
    try:
        result = subprocess.run([str(arg) for arg in args], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NcloudError("cannot execute {}: {}".format(args[0], exc))
    if check and result.returncode:
        raise NcloudError("{} failed ({}): {}".format(args[0], result.returncode, (result.stderr or result.stdout).strip()[-1800:]))
    return result


def require_linux_root():
    if platform.system() != "Linux":
        raise NcloudError("this command must run on the Linux VPS; Windows is for development and client export")
    if os.geteuid() != 0:
        raise NcloudError("run this command with sudo or as root")
    if not Path("/run/systemd/system").is_dir() or not shutil.which("systemctl"):
        raise NcloudError("a running systemd instance is required")


@contextlib.contextmanager
def operation_lock():
    import fcntl
    with open("/run/ncloud.lock", "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise NcloudError("another ncloud operation is already running")
        yield


def sha256_file(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def platform_key():
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if arch is None:
        raise NcloudError("unsupported CPU architecture: " + machine)
    return platform.system().lower() + "-" + arch


def download_core(version, directory, target_platform=None):
    target_platform = target_platform or platform_key()
    release = RELEASES.get(version, {}).get(target_platform)
    if not release:
        raise NcloudError("unreviewed Xray version or platform; update ncloud from Git to obtain a reviewed manifest")
    name, expected = release
    archive = Path(directory) / name
    url = "https://github.com/XTLS/Xray-core/releases/download/v{}/{}".format(version, name)
    request = urllib.request.Request(url, headers={"User-Agent": "ncloud/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            size = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 150 * 1024 * 1024:
                    raise NcloudError("upstream archive exceeds size limit")
                output.write(chunk)
    except OSError as exc:
        raise NcloudError("Xray download failed: " + str(exc))
    if sha256_file(archive) != expected:
        raise NcloudError("Xray SHA-256 mismatch; installation aborted")
    member = "xray.exe" if target_platform.startswith("windows") else "xray"
    binary = Path(directory) / member
    try:
        with zipfile.ZipFile(str(archive)) as zipped:
            info = zipped.getinfo(member)
            if info.file_size > 200 * 1024 * 1024:
                raise NcloudError("upstream binary exceeds size limit")
            # Copy only the named binary; never extract arbitrary archive paths.
            with zipped.open(info) as source, binary.open("wb") as output:
                shutil.copyfileobj(source, output)
    except (KeyError, zipfile.BadZipFile) as exc:
        raise NcloudError("invalid upstream archive: " + str(exc))
    binary.chmod(0o755)
    run([binary, "version"])
    return binary


def generate_keys(binary):
    result = run([binary, "x25519"])
    values = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            label, value = line.split(":", 1)
            values[re.sub(r"[^a-z]", "", label.lower())] = value.strip()
    private = values.get("privatekey")
    public = values.get("publickey") or values.get("passwordpublickey") or values.get("password")
    if private is None or public is None:
        raise NcloudError("unexpected Xray x25519 output; no state was written")
    return key32(private), key32(public)


def target_check(name, timeout=12):
    addresses = socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
    import ipaddress
    if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise NcloudError("REALITY target resolved to a non-public address")
    context = ssl.create_default_context()
    if not hasattr(ssl.TLSVersion, "TLSv1_3"):
        raise NcloudError("Python/OpenSSL must support TLS 1.3")
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2"])
    with socket.create_connection((name, 443), timeout=timeout) as tcp:
        with context.wrap_socket(tcp, server_hostname=name) as tls:
            if tls.version() != "TLSv1.3" or tls.selected_alpn_protocol() != "h2":
                raise NcloudError("REALITY target must support TLS 1.3 and HTTP/2")
            return {"tls": tls.version(), "alpn": tls.selected_alpn_protocol()}


def atomic_write(path, content, mode=0o600, gid=None):
    path = Path(path)
    if path.is_symlink():
        raise NcloudError("refusing to replace a symlink: " + str(path))
    descriptor, temporary = tempfile.mkstemp(prefix=".ncloud-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.chmod(temporary, mode)
            if gid is not None:
                os.chown(temporary, 0, gid)
            handle.write(content.encode("utf-8") if isinstance(content, str) else content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def service_unit(binary, config):
    return """# Managed by ncloud
[Unit]
Description=ncloud personal Xray REALITY server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User=ncloud
Group=ncloud
ExecStart={binary} run -config {config}
Restart=on-failure
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
""".format(binary=binary, config=config)


def ensure_service_user():
    import grp
    import pwd
    try:
        user = pwd.getpwnam("ncloud")
    except KeyError:
        run(["useradd", "--system", "--user-group", "--no-create-home", "--shell", "/sbin/nologin", "ncloud"])
        user = pwd.getpwnam("ncloud")
    try:
        group = grp.getgrnam("ncloud")
    except KeyError:
        raise NcloudError("existing ncloud user has no ncloud group")
    if user.pw_uid == 0 or user.pw_uid >= 1000 or user.pw_gid != group.gr_gid:
        raise NcloudError("existing ncloud account is not a dedicated system service account")
    return group.gr_gid


class Firewall:
    """Only add a requested firewalld port; never flush rules or disable SELinux."""

    def __init__(self, port):
        self.port = str(port) + "/tcp"
        self.added = []
        self.zone = None

    def open(self):
        if not shutil.which("firewall-cmd") or run(["firewall-cmd", "--state"], check=False).returncode:
            raise NcloudError("--open-firewall needs active firewalld; configure your existing firewall manually")
        # Require one active zone; choosing a default zone can silently open the wrong interface.
        zones = run(["firewall-cmd", "--get-active-zones"]).stdout.splitlines()
        zones = [line.strip() for line in zones if line and not line[0].isspace()]
        if len(zones) != 1:
            raise NcloudError("multiple/no active firewalld zones; configure the port in the correct zone manually")
        self.zone = zones[0]
        for permanent in (False, True):
            base = ["firewall-cmd", "--zone=" + self.zone] + (["--permanent"] if permanent else [])
            query = run(base + ["--query-port=" + self.port], check=False)
            if query.returncode not in (0, 1):
                raise NcloudError("cannot inspect firewalld port rules")
            if query.returncode == 1:
                run(base + ["--add-port=" + self.port])
                self.added.append(base)

    def rollback(self):
        for base in reversed(self.added):
            run(base + ["--remove-port=" + self.port], check=False)
        self.added = []
