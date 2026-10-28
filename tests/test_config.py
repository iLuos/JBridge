import json
import unittest

from ncloud.config import NcloudError, client_config, hostname, port_number, public_ipv4, server_config, validate_state
from .helpers import state


class ConfigTests(unittest.TestCase):
    def test_server_credentials_and_private_network_block(self):
        value = state()
        config = server_config(value)
        inbound = config["inbounds"][0]
        self.assertEqual(inbound["settings"]["clients"][0]["id"], value["uuid"])
        self.assertEqual(inbound["streamSettings"]["realitySettings"]["privateKey"], value["private_key"])
        self.assertEqual(inbound["streamSettings"]["realitySettings"]["target"], "www.microsoft.com:443")
        self.assertEqual(config["routing"]["rules"][0]["outboundTag"], "block")
        self.assertIn("169.254.0.0/16", config["routing"]["rules"][0]["ip"])
        # Routing must see the requested destination, not a sniffed public SNI
        # which could conceal an originally private destination address.
        self.assertNotIn("sniffing", inbound)

    def test_client_never_contains_private_key(self):
        value = state()
        config = client_config(value)
        encoded = json.dumps(config)
        self.assertNotIn(value["private_key"], encoded)
        self.assertNotIn("privateKey", encoded)
        self.assertEqual(config["proxies"][0]["reality-opts"]["public-key"], value["public_key"])
        self.assertTrue(config["proxies"][0]["reality-opts"]["support-x25519mlkem768"])
        self.assertFalse(config["allow-lan"])
        self.assertEqual(config["bind-address"], "127.0.0.1")

    def test_rule_order_and_no_direct_proxy_fallback(self):
        config = client_config(state())
        rules = config["rules"]
        self.assertLess(rules.index("RULE-SET,cn-domain,DIRECT"), rules.index("MATCH,日本节点"))
        self.assertEqual(rules[-1], "MATCH,日本节点")
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["日本 VPS"])
        self.assertTrue(config["tun"]["strict-route"])
        self.assertTrue(config["ipv6"])
        self.assertIn("#日本节点", config["dns"]["nameserver"][0])
        self.assertIn("rule-set:cn-domain", config["dns"]["nameserver-policy"])
        self.assertFalse(config["dns"]["use-system-hosts"])

    def test_reject_bad_input(self):
        for value in (0, 65536, -1, "1;rm", "1.5", True, None):
            with self.subTest(port=value), self.assertRaises(NcloudError):
                port_number(value)
        for value in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "224.0.0.1", "::1", "example.com"):
            with self.subTest(ip=value), self.assertRaises(NcloudError):
                public_ipv4(value)
        for value in ("https://example.com", "example.com:443", "x\n.test", "-x.test", "127.0.0.1"):
            with self.subTest(host=value), self.assertRaises(NcloudError):
                hostname(value)

    def test_reject_incomplete_or_malformed_credentials(self):
        for key, value in (("uuid", "broken"), ("short_id", "gg"), ("private_key", "A" * 42),
                           ("public_key", "!" * 43), ("binary_sha256", "xyz"), ("schema_version", 9)):
            candidate = state()
            candidate[key] = value
            with self.subTest(field=key), self.assertRaises(NcloudError):
                validate_state(candidate)
        with self.assertRaises(NcloudError):
            validate_state({"schema_version": 1})
