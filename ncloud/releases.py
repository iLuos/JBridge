"""Reviewed upstream archive digests; update this manifest through Git releases."""

DEFAULT_VERSION = "26.10.21"
RELEASES = {
    "26.10.21": {
        "linux-amd64": ("Xray-linux-64.zip", "0eec3ed4163c273d8c8bf821f7d219e257921cf584aa3fb970715e458e631978"),
        "linux-arm64": ("Xray-linux-arm64-v8a.zip", "d1304946c486d98e6b886e9b931eaaaebb23835b30e217638037b7f2721d4e9c"),
        "windows-amd64": ("Xray-windows-64.zip", "fc58c39ee2f2717b4c54d28d374362389e5319b20ae44aea76ea45a5243ec827"),
    },
}
