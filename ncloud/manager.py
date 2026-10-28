"""Transactional service lifecycle for a single personal node."""

import contextlib
import datetime
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import tempfile
import time
import uuid

from .config import NcloudError, client_config, hostname, json_text, port_number, public_ipv4, server_config, validate_state
from .platforms import (Firewall, SERVICE, UNIT_MARKER, atomic_write, download_core, ensure_service_user,
                        generate_keys, operation_lock, require_linux_root, run, service_unit, sha256_file, target_check)
from .releases import DEFAULT_VERSION

CONFIG_DIR = Path("/etc/ncloud")
STATE = CONFIG_DIR / "state.json"
CONFIG = CONFIG_DIR / "xray.json"
BINARY_DIR = Path("/usr/local/libexec/ncloud")
BINARY = BINARY_DIR / "xray"
UNIT = Path("/etc/systemd/system") / SERVICE
BACKUPS = Path("/var/backups/ncloud")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_state(path=None):
    path = Path(STATE if path is None else path)
    if path.is_symlink():
        raise NcloudError("state file must not be a symlink")
    if path.stat().st_size > 64 * 1024:
        raise NcloudError("state file exceeds size limit")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise NcloudError("state file permissions must be 0600 (chmod 600)")
    try:
        return validate_state(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, UnicodeError):
        raise NcloudError("state file is not valid UTF-8 JSON")


def check_owned():
    for directory in (CONFIG_DIR, BINARY_DIR, BACKUPS):
        if directory.is_symlink():
            raise NcloudError("managed directory must not be a symlink: " + str(directory))
    for path in (STATE, CONFIG, BINARY, UNIT):
        if path.is_symlink():
            raise NcloudError("managed file must not be a symlink: " + str(path))
    if UNIT.exists() and not UNIT.read_text(encoding="utf-8").startswith(UNIT_MARKER + "\n"):
        raise NcloudError("existing systemd unit is not owned by ncloud")
    if not STATE.exists() and any(path.exists() for path in (CONFIG, BINARY, UNIT)):
        raise NcloudError("incomplete installation without state; inspect existing ncloud files before proceeding")


def read_configuration():
    if CONFIG.stat().st_size > 1024 * 1024:
        raise NcloudError("installed configuration exceeds size limit")
    try:
        result = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise NcloudError("installed Xray configuration is not valid UTF-8 JSON")
    if not isinstance(result, dict):
        raise NcloudError("installed Xray configuration must be a JSON object")
    return result


def prepare_directories(gid):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(str(CONFIG_DIR), 0, gid)
    CONFIG_DIR.chmod(0o750)
    BINARY_DIR.mkdir(parents=True, exist_ok=True)
    BINARY_DIR.chmod(0o755)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    BACKUPS.chmod(0o700)


def is_active():
    return run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0


def is_enabled():
    return run(["systemctl", "is-enabled", "--quiet", SERVICE], check=False).returncode == 0


class Snapshot:
    def __init__(self):
        BACKUPS.mkdir(parents=True, exist_ok=True)
        BACKUPS.chmod(0o700)
        self.path = Path(tempfile.mkdtemp(prefix=datetime.datetime.now().strftime("%Y%m%d-%H%M%S-"), dir=str(BACKUPS)))
        self.path.chmod(0o700)
        self.active = is_active()
        self.enabled = is_enabled()
        self.entries = []
        for index, path in enumerate((BINARY, CONFIG, STATE, UNIT)):
            entry = {"path": str(path), "existed": path.exists(), "copy": str(index)}
            if path.exists():
                info = path.stat()
                entry.update(mode=info.st_mode & 0o777, uid=info.st_uid, gid=info.st_gid)
                shutil.copy2(str(path), str(self.path / str(index)))
                (self.path / str(index)).chmod(0o600)
            self.entries.append(entry)
        atomic_write(self.path / "manifest.json", json_text({
            "created_at": utc_now(), "active": self.active, "enabled": self.enabled, "files": self.entries,
        }))

    def restore(self):
        failures = []
        # Stop the candidate before restoring files, including when its first start failed.
        try:
            run(["systemctl", "stop", SERVICE], check=False)
        except NcloudError as exc:
            failures.append(str(exc))
        for entry in self.entries:
            path = Path(entry["path"])
            try:
                if entry["existed"]:
                    atomic_write(path, (self.path / entry["copy"]).read_bytes(), entry["mode"], entry["gid"])
                    os.chown(str(path), entry["uid"], entry["gid"])
                elif path.exists():
                    path.unlink()
            except (OSError, NcloudError) as exc:
                failures.append(str(exc))
        for command in (["systemctl", "daemon-reload"],
                        ["systemctl", "enable" if self.enabled else "disable", SERVICE]):
            try:
                # Missing units after a failed fresh installation are expected on disable.
                run(command, check=command[1] != "disable")
            except NcloudError as exc:
                failures.append(str(exc))
        if self.active:
            try:
                run(["systemctl", "restart", SERVICE])
                if not is_active():
                    raise NcloudError("previous service did not resume")
            except NcloudError as exc:
                failures.append(str(exc))
        if failures:
            raise NcloudError("rollback needs manual attention; backup {}: {}".format(self.path, "; ".join(failures)))


@contextlib.contextmanager
def transaction(firewall=None):
    snapshot = Snapshot()
    try:
        yield snapshot
    except BaseException as exc:
        errors = []
        if firewall is not None:
            try:
                firewall.rollback()
            except (NcloudError, OSError) as rollback_error:
                errors.append(str(rollback_error))
        try:
            snapshot.restore()
        except (NcloudError, OSError) as rollback_error:
            errors.append(str(rollback_error))
        if errors:
            raise NcloudError("operation failed; rollback errors: " + "; ".join(errors)) from exc
        raise NcloudError("operation failed; previous installation restored. Backup: {}. Cause: {}".format(snapshot.path, exc)) from exc


def check_port(port, previous=None):
    if previous and previous["port"] == port and is_active():
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("0.0.0.0", port))
        except OSError:
            raise NcloudError("TCP port {} is occupied; choose another port with --port (for example 8443)".format(port))


def test_config(binary, configuration):
    with tempfile.TemporaryDirectory(prefix="ncloud-check-") as directory:
        path = Path(directory) / "xray.json"
        atomic_write(path, json_text(configuration))
        run([binary, "run", "-test", "-config", path])


def start_and_check(port):
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", SERVICE])
    run(["systemctl", "restart", SERVICE])
    # A successful systemctl restart alone does not prove that Xray stayed alive.
    for _ in range(3):
        time.sleep(1)
        if not is_active():
            raise NcloudError("Xray exited after startup; inspect journalctl -u " + SERVICE)
    with socket.create_connection(("127.0.0.1", port), timeout=3):
        pass
    restarts = run(["systemctl", "show", SERVICE, "--property=NRestarts", "--value"]).stdout.strip()
    if restarts != "0":
        raise NcloudError("Xray restarted during health check")


def init_node(server=None, port=None, server_name=None, version=None, open_firewall=False):
    require_linux_root()
    with operation_lock():
        check_owned()
        previous = load_state() if STATE.exists() else None
        if server is None and previous is None:
            raise NcloudError("first initialization requires --server YOUR_PUBLIC_IPV4")
        server = public_ipv4(server if server is not None else previous["server"])
        port = port_number(port if port is not None else (previous["port"] if previous else 443))
        server_name = hostname(server_name if server_name is not None else (previous["server_name"] if previous else "www.cloudflare.com"))
        version = version or (previous["xray_version"] if previous else DEFAULT_VERSION)
        check_port(port, previous)
        print("Checking REALITY target TLS 1.3 / HTTP/2...")
        target_check(server_name)
        with tempfile.TemporaryDirectory(prefix="ncloud-install-") as directory:
            if previous and BINARY.is_file() and previous["xray_version"] == version and sha256_file(BINARY) == previous["binary_sha256"]:
                binary = BINARY
            else:
                print("Downloading and verifying Xray v{}...".format(version))
                binary = download_core(version, directory)
            if previous:
                state = dict(previous)
            else:
                private, public = generate_keys(binary)
                state = {"schema_version": 1, "uuid": str(uuid.uuid4()), "private_key": private,
                         "public_key": public, "short_id": secrets.token_hex(8), "created_at": utc_now()}
            state.update(server=server, port=port, server_name=server_name, xray_version=version,
                         binary_sha256=sha256_file(binary), updated_at=utc_now())
            configuration = server_config(state)
            test_config(binary, configuration)
            gid = ensure_service_user()
            prepare_directories(gid)
            firewall = Firewall(port) if open_firewall else None
            with transaction(firewall) as snapshot:
                if binary != BINARY:
                    atomic_write(BINARY, binary.read_bytes(), 0o755)
                atomic_write(CONFIG, json_text(configuration), 0o640, gid)
                atomic_write(STATE, json_text(state))
                atomic_write(UNIT, service_unit(BINARY, CONFIG), 0o644)
                if shutil.which("restorecon"):
                    run(["restorecon", "-RF", str(CONFIG_DIR), str(BINARY_DIR), str(UNIT)])
                if firewall:
                    firewall.open()
                start_and_check(port)
            print("Node ready: {}:{}; backup: {}".format(server, port, snapshot.path))
            print("Allow TCP {} in the Alibaba Cloud security group and host firewall.".format(port))
            print("Export: sudo ncloud export --output /root/ncloud-client.yaml")
            if previous and previous["port"] != port:
                print("After client migration, remove the old TCP {} rule manually.".format(previous["port"]))


def update_node(version=None):
    require_linux_root()
    with operation_lock():
        check_owned()
        state = load_state()
        version = version or DEFAULT_VERSION
        if state["xray_version"] == version and BINARY.exists() and sha256_file(BINARY) == state["binary_sha256"]:
            print("Already running reviewed Xray v{}; no change.".format(version))
            return
        with tempfile.TemporaryDirectory(prefix="ncloud-update-") as directory:
            binary = download_core(version, directory)
            test_config(binary, read_configuration())
            with transaction() as snapshot:
                atomic_write(BINARY, binary.read_bytes(), 0o755)
                if shutil.which("restorecon"):
                    run(["restorecon", str(BINARY)])
                state.update(xray_version=version, binary_sha256=sha256_file(BINARY), updated_at=utc_now())
                atomic_write(STATE, json_text(state))
                start_and_check(state["port"])
                if not snapshot.active:
                    run(["systemctl", "stop", SERVICE])
                if not snapshot.enabled:
                    run(["systemctl", "disable", SERVICE])
            print("Updated to v{}; backup: {}".format(version, snapshot.path))


def export_client(output, state_path=None):
    path = Path(output).expanduser().absolute()
    source = Path(STATE if state_path is None else state_path).expanduser().absolute()
    if path == source or path in (CONFIG, BINARY, UNIT):
        raise NcloudError("export must not overwrite a managed file")
    if path.exists() or path.is_symlink():
        raise NcloudError("output already exists; choose a new filename")
    config = client_config(load_state(source))
    # Exclusive creation prevents following a newly introduced symlink.
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json_text(config))
    except BaseException:
        path.unlink()
        raise
    print("Client profile written to {} (contains credentials; keep private).".format(path))


def lifecycle(action):
    require_linux_root()
    with operation_lock():
        check_owned()
        state = load_state()
        if action == "start":
            test_config(BINARY, read_configuration())
            start_and_check(state["port"])
        else:
            run(["systemctl", "stop", SERVICE])
        print("Service " + ("running" if is_active() else "stopped"))


def status():
    require_linux_root()
    check_owned()
    if not STATE.exists():
        print("Not initialized. Run ncloud init --server YOUR_PUBLIC_IPV4")
        return 1
    state = load_state()
    active = is_active()
    print(json_text({"service": SERVICE, "active": active, "enabled": is_enabled(),
                     "server": state["server"], "port": state["port"], "server_name": state["server_name"],
                     "xray_version": state["xray_version"], "updated_at": state.get("updated_at")}))
    return 0 if active else 1


def doctor():
    require_linux_root()
    check_owned()
    state = load_state()
    checks = []
    def check(name, operation):
        try:
            result = operation()
            if result is False:
                raise NcloudError("check failed")
            checks.append({"check": name, "ok": True})
        except (OSError, NcloudError) as exc:
            checks.append({"check": name, "ok": False, "detail": str(exc)})
    check("binary checksum", lambda: BINARY.exists() and sha256_file(BINARY) == state["binary_sha256"])
    check("Xray configuration", lambda: test_config(BINARY, read_configuration()))
    check("systemd service", is_active)
    def listener():
        with socket.create_connection(("127.0.0.1", state["port"]), timeout=3):
            return True
    check("local TCP listener", listener)
    check("REALITY target TLS 1.3 / h2", lambda: target_check(state["server_name"]))
    clock = run(["timedatectl", "show", "--property=NTPSynchronized", "--value"], check=False)
    checks.append({"check": "clock synchronization", "ok": clock.returncode == 0 and clock.stdout.strip() == "yes"})
    print(json_text({"checks": checks, "external_checks_required": [
        "Alibaba Cloud security group and host firewall allow TCP {}".format(state["port"]),
        "Windows client connects from your current network", "China DIRECT / Japan proxy, DNS and IPv6 behavior",
    ]}))
    return 0 if all(item["ok"] for item in checks) else 1


def uninstall(purge=False):
    require_linux_root()
    with operation_lock():
        check_owned()
        if not STATE.exists():
            print("No initialized node found.")
            return
        with transaction() as snapshot:
            if UNIT.exists():
                run(["systemctl", "stop", SERVICE])
                run(["systemctl", "disable", SERVICE])
                UNIT.unlink()
            if BINARY.exists():
                BINARY.unlink()
            run(["systemctl", "daemon-reload"])
            if purge:
                for path in (CONFIG, STATE):
                    if path.exists():
                        path.unlink()
        print("Service/core removed; backup: {}".format(snapshot.path))
        print("Management CLI, service account, backups and firewall rules retained.")
        if not purge:
            print("Node identity retained in /etc/ncloud; ncloud init will reuse it.")
        else:
            print("Active node configuration removed. Backups still contain old credentials.")
