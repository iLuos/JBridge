import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from ncloud.config import NcloudError
from ncloud.platforms import Firewall, atomic_write, download_core, generate_keys, service_unit
from .helpers import state


class PlatformTests(unittest.TestCase):
    def test_parses_current_and_legacy_x25519_output(self):
        value = state()
        for private_label, public_label in (("PrivateKey", "Password (PublicKey)"), ("PrivateKey", "Password"), ("Private key", "Public key")):
            text = "{}: {}\n{}: {}\nHash32: ignored\n".format(private_label, value["private_key"], public_label, value["public_key"])
            with patch("ncloud.platforms.run", return_value=SimpleNamespace(stdout=text)):
                self.assertEqual(generate_keys("xray"), (value["private_key"], value["public_key"]))

    def test_checksum_failure_never_extracts_or_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"modified archive")), patch("ncloud.platforms.run") as execute:
                with self.assertRaisesRegex(NcloudError, "SHA-256"):
                    download_core("26.10.21", directory, "linux-amd64")
                execute.assert_not_called()
                self.assertFalse((Path(directory) / "xray").exists())

    def test_unreviewed_version_never_downloads(self):
        with patch("urllib.request.urlopen") as download:
            with self.assertRaises(NcloudError):
                download_core("99.1.1", ".", "linux-amd64")
            download.assert_not_called()

    def test_only_extracts_expected_binary(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("xray", b"test binary")
            zipped.writestr("../escaped", b"bad")
        payload = archive.getvalue()
        import hashlib
        manifest = {"1.2.3": {"linux-amd64": ("test.zip", hashlib.sha256(payload).hexdigest())}}
        with tempfile.TemporaryDirectory() as directory:
            with patch("ncloud.platforms.RELEASES", manifest), patch("urllib.request.urlopen", return_value=io.BytesIO(payload)), patch("ncloud.platforms.run"):
                binary = download_core("1.2.3", directory, "linux-amd64")
                self.assertEqual(binary.read_bytes(), b"test binary")
                self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["test.zip", "xray"])

    def test_atomic_write_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            atomic_write(path, "old")
            atomic_write(path, "new")
            self.assertEqual(path.read_text(), "new")
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_service_is_unprivileged_and_capability_limited(self):
        text = service_unit("/usr/local/libexec/ncloud/xray", "/etc/ncloud/xray.json")
        self.assertIn("User=ncloud", text)
        self.assertIn("CapabilityBoundingSet=CAP_NET_BIND_SERVICE\n", text)
        self.assertNotIn("CAP_NET_ADMIN", text)
        self.assertIn("ProtectSystem=strict", text)

    def test_firewall_rollback_removes_only_added_rule(self):
        calls = []
        def execute(args, **kwargs):
            calls.append(args)
            if "--get-active-zones" in args:
                return SimpleNamespace(stdout="public\n  interfaces: eth0\n", returncode=0)
            if "--query-port=443/tcp" in args:
                return SimpleNamespace(stdout="", returncode=0 if "--permanent" in args else 1)
            return SimpleNamespace(stdout="", returncode=0)
        with patch("ncloud.platforms.run", side_effect=execute), patch("shutil.which", return_value="/bin/firewall-cmd"):
            firewall = Firewall(443)
            firewall.open()
            firewall.rollback()
        removals = [args for args in calls if "--remove-port=443/tcp" in args]
        self.assertEqual(len(removals), 1)
        self.assertNotIn("--permanent", removals[0])
