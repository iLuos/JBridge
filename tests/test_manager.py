import contextlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ncloud import manager
from ncloud.config import NcloudError, json_text
from ncloud.platforms import atomic_write
from .helpers import state


class ManagerTests(unittest.TestCase):
    def test_export_excludes_server_private_key_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "state.json"
            output = Path(directory) / "client.yaml"
            atomic_write(source, json_text(state()))
            manager.export_client(output, source)
            client = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(client["proxies"][0]["uuid"], state()["uuid"])
            self.assertNotIn(state()["private_key"], output.read_text(encoding="utf-8"))
            before = output.read_bytes()
            with self.assertRaisesRegex(NcloudError, "already exists"):
                manager.export_client(output, source)
            self.assertEqual(output.read_bytes(), before)
            with self.assertRaises(NcloudError):
                manager.export_client(source, source)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_state_world_readable_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "state.json"
            atomic_write(source, json_text(state()))
            source.chmod(0o644)
            with self.assertRaisesRegex(NcloudError, "0600"):
                manager.load_state(source)

    def test_port_in_use_is_rejected_without_changing_listener(self):
        import socket
        with socket.socket() as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("0.0.0.0", 0))
            listener.listen()
            with self.assertRaisesRegex(NcloudError, "occupied"):
                manager.check_port(listener.getsockname()[1])
            self.assertNotEqual(listener.fileno(), -1)

    def test_failed_transaction_restores_old_files(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            for name in ("STATE", "CONFIG", "BINARY", "UNIT"):
                path = root / name.lower()
                path.write_text("old-" + name)
                stack.enter_context(patch.object(manager, name, path))
            stack.enter_context(patch.object(manager, "BACKUPS", root / "backups"))
            stack.enter_context(patch.object(manager, "is_active", return_value=False))
            stack.enter_context(patch.object(manager, "is_enabled", return_value=False))
            stack.enter_context(patch.object(manager, "run"))
            # Windows has no uid/gid/chown. Patches affect only this mock lifecycle test.
            stack.enter_context(patch("os.chown", create=True))
            with self.assertRaisesRegex(NcloudError, "previous installation restored"):
                with manager.transaction():
                    for name in ("STATE", "CONFIG", "BINARY", "UNIT"):
                        getattr(manager, name).write_text("new")
                    raise NcloudError("candidate startup failed")
            for name in ("STATE", "CONFIG", "BINARY", "UNIT"):
                self.assertEqual(getattr(manager, name).read_text(), "old-" + name)

    def test_failed_fresh_install_removes_new_managed_files(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            for name in ("STATE", "CONFIG", "BINARY", "UNIT"):
                stack.enter_context(patch.object(manager, name, root / name.lower()))
            stack.enter_context(patch.object(manager, "BACKUPS", root / "backups"))
            stack.enter_context(patch.object(manager, "is_active", return_value=False))
            stack.enter_context(patch.object(manager, "is_enabled", return_value=False))
            stack.enter_context(patch.object(manager, "run"))
            with self.assertRaises(NcloudError):
                with manager.transaction():
                    manager.STATE.write_text("secret")
                    manager.UNIT.write_text("candidate")
                    raise NcloudError("start failed")
            self.assertFalse(manager.STATE.exists())
            self.assertFalse(manager.UNIT.exists())

    def test_init_reuses_identity_and_selected_port(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            paths = {name: root / name.lower() for name in ("STATE", "CONFIG", "BINARY", "UNIT")}
            for name, path in paths.items():
                stack.enter_context(patch.object(manager, name, path))
            old = state()
            old["port"] = 8443
            paths["BINARY"].write_bytes(b"binary")
            from ncloud.platforms import sha256_file
            old["binary_sha256"] = sha256_file(paths["BINARY"])
            atomic_write(paths["STATE"], json_text(old))
            stack.enter_context(patch.object(manager, "require_linux_root"))
            stack.enter_context(patch.object(manager, "operation_lock", contextlib.nullcontext))
            stack.enter_context(patch.object(manager, "check_owned"))
            stack.enter_context(patch.object(manager, "check_port"))
            stack.enter_context(patch.object(manager, "target_check"))
            stack.enter_context(patch.object(manager, "test_config"))
            stack.enter_context(patch.object(manager, "ensure_service_user", return_value=None))
            stack.enter_context(patch.object(manager, "prepare_directories"))
            stack.enter_context(patch.object(manager, "transaction", return_value=contextlib.nullcontext(type("Backup", (), {"path": "backup"})())))
            stack.enter_context(patch.object(manager, "start_and_check"))
            stack.enter_context(patch("shutil.which", return_value=None))
            keys = stack.enter_context(patch.object(manager, "generate_keys"))
            download = stack.enter_context(patch.object(manager, "download_core"))
            manager.init_node()
            new = manager.load_state(paths["STATE"])
            for field in ("uuid", "private_key", "public_key", "short_id", "port", "server"):
                self.assertEqual(new[field], old[field])
            keys.assert_not_called()
            download.assert_not_called()
