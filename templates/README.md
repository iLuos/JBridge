# Configuration templates

配置由 `ncloud/config.py` 中的纯函数生成，避免维护两份可能不一致的模板。

- `server_config(state)`：Xray VLESS + REALITY + Vision 服务端 JSON。
- `client_config(state)`：Clash Verge Rev / Mihomo 客户端配置，导出为 YAML 1.2 兼容的 JSON 文本。

此目录不存放真实配置、UUID、密钥或导出的客户端文件。
