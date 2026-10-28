"""Small explicit command surface; no web panel or public admin endpoint."""

import argparse
import sys

from . import __version__
from .config import NcloudError
from . import manager


def parser():
    result = argparse.ArgumentParser(prog="ncloud", description="Personal Xray REALITY node manager")
    result.add_argument("--version", action="version", version="ncloud " + __version__)
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="install/configure a Linux VPS node (root)")
    init.add_argument("--server", help="VPS public IPv4; required on first init")
    init.add_argument("--port", type=int, help="TCP listener (first init default: 443)")
    init.add_argument("--server-name", help="REALITY TLS target hostname (default: www.cloudflare.com)")
    init.add_argument("--xray-version", help="reviewed version from the bundled manifest")
    init.add_argument("--open-firewall", action="store_true", help="add the port in the sole active firewalld zone")
    for command in ("start", "stop", "status", "doctor"):
        commands.add_parser(command, help="{} the managed Linux service (root)".format(command))
    export = commands.add_parser("export", help="write a private Clash Verge Rev profile")
    export.add_argument("--output", required=True, help="new output file; never overwrites existing files")
    export.add_argument("--state", default=str(manager.STATE), help="private state file (default: /etc/ncloud/state.json)")
    update = commands.add_parser("update", help="update Xray transactionally (root)")
    update.add_argument("--xray-version", help="reviewed version (default: bundled recommended release)")
    uninstall = commands.add_parser("uninstall", help="remove service/core, retain CLI and backups (root)")
    uninstall.add_argument("--purge", action="store_true", help="also remove live state/config; backups retained")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            manager.init_node(args.server, args.port, args.server_name, args.xray_version, args.open_firewall)
        elif args.command in ("start", "stop"):
            manager.lifecycle(args.command)
        elif args.command == "status":
            return manager.status()
        elif args.command == "doctor":
            return manager.doctor()
        elif args.command == "export":
            manager.export_client(args.output, args.state)
        elif args.command == "update":
            manager.update_node(args.xray_version)
        elif args.command == "uninstall":
            manager.uninstall(args.purge)
        return 0
    except (NcloudError, OSError) as exc:
        print("ncloud: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ncloud: interrupted", file=sys.stderr)
        return 130
