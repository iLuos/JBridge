import base64


def state():
    # Deliberately synthetic credentials, never used by a deployed node.
    return {
        "schema_version": 1, "server": "8.8.8.8", "port": 443,
        "server_name": "www.microsoft.com", "uuid": "df3b78d1-99c4-455f-b8ea-81cbaab47db8",
        "private_key": base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
        "public_key": base64.urlsafe_b64encode(bytes(range(32, 64))).decode().rstrip("="),
        "short_id": "0123456789abcdef", "xray_version": "26.10.21",
        "binary_sha256": "0" * 64, "created_at": "2026-01-01T00:00:00+00:00",
    }
