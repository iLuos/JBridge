# ncloud / JBridge

个人日本 VPS 节点管理工具：Xray **VLESS + REALITY + Vision** 服务端，Windows 使用 **Clash Verge Rev / Mihomo**，支持国内直连、其他流量经日本 VPS。

第一版提供 CLI 和客户端配置生成，不包含自制 Windows GUI 或 Web 管理面板。Xray 负责协议及加密，ncloud 负责部署和维护。连接效果仍需在实际网络中测试。

## 共享客户端配置

仓库提供可直接连接现有节点的[客户端配置文件](profiles/ncloud-client.yaml)。在文件页面下载原始文件后，Windows 和 macOS 用户可在 [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev/releases/latest) 中导入并选中，使用“规则”模式、选择“日本 VPS”，安装客户端服务并开启 TUN。

现有共享节点使用 **TCP 8443**。此前导入 443 端口配置的用户，需要重新下载并导入此文件，或手动将“日本 VPS”节点端口改为 `8443`。

该文件包含有效的共享节点访问凭据，公开文件的任何持有人都可以使用对应 VPS。所有使用者共用带宽、流量和同一组凭据；更换节点或撤销共享凭据后，需要同步更新此文件。服务端私钥和 SSH 私钥不在该配置中。

## 环境

- 目标系统：Alibaba Cloud Linux 3、运行 systemd、root/sudo；其他 systemd Linux 需要自行验收。
- Python **3.8+**，仅标准库；安装器优先寻找 Python 3.11，不替换系统 Python。
- CPU：Linux x86_64 或 aarch64；公网 IPv4；GitHub Release 下载可用。
- 默认 TCP 443；已有其他服务占用时显式选择 8443 等端口。
- 默认 REALITY 目标为 `www.cloudflare.com:443`，安装前检查证书、TLS 1.3、HTTP/2；可用 `--server-name` 指定其他经验证的目标。
- 当前固定 Xray **26.10.21**。归档 SHA-256 位于 `ncloud/releases.py`，来源为官方 GitHub Release digest；未列入清单的版本不会安装。

## 从 GitHub 安装

以下命令在日本 VPS 上执行。私有仓库需要先配置只读 Deploy Key；不要在 clone URL 中写入令牌。

```bash
git clone https://github.com/iLuos/JBridge.git
cd JBridge
# 安装管理程序之前，阅读 install.sh 和本项目代码。
sudo bash install.sh

# 将 YOUR_PUBLIC_IPV4 替换为 VPS 控制台显示的公网 IPv4。
sudo ncloud init --server YOUR_PUBLIC_IPV4
sudo ncloud status
sudo ncloud doctor
sudo ncloud export --output /root/ncloud-client.yaml
```

`install.sh` 先运行离线测试，再将管理程序放到 `/opt/ncloud`，创建 `/usr/local/bin/ncloud`。它不会自动初始化节点。若缺少 Python，dnf 安装候选为 `python3.11`、`python39`、`python3`；若仓库不能提供 3.8+ 则退出，需先配置系统支持的软件源。

443 被占用时：

```bash
sudo ncloud init --server YOUR_PUBLIC_IPV4 --port 8443
```

### 阿里云安全组与系统防火墙

在阿里云控制台给实例绑定的安全组添加**入方向 TCP 443**（或选定端口）。动态家庭公网 IP 通常需要允许任意来源；固定来源可缩小范围。不要删除现有 SSH 规则。

如果系统运行 firewalld 且只有一个活动区域，可以选择：

```bash
sudo ncloud init --server YOUR_PUBLIC_IPV4 --open-firewall
```

该选项仅添加所选 TCP 端口到活动区域的运行时及永久规则，不 reload/清空规则。非 firewalld、多区域或没有活动区域时会退出并回滚，请根据实际防火墙手动配置。云安全组始终需要单独检查。默认 `init` 不修改防火墙，SELinux 保持现有状态。

## Windows 客户端

1. 从 [Clash Verge Rev 官方发布页](https://github.com/clash-verge-rev/clash-verge-rev/releases)安装稳定版。
2. 通过 SCP/SFTP 将 VPS 的 `/root/ncloud-client.yaml` 下载至本机的私密目录；自定义 SSH 用户/端口时相应调整。

   ```powershell
   # 本地文件夹应限制为自己的 Windows 账户访问；不要放到 Git 仓库或公共共享目录。
   scp root@YOUR_PUBLIC_IPV4:/root/ncloud-client.yaml "$env:USERPROFILE\Downloads\ncloud-client.yaml"
   ```

3. 在客户端的“配置”页面导入本地文件，选择该配置，将“日本节点”设为“日本 VPS”。
4. 使用“规则”模式，安装客户端服务并开启 TUN；关闭其他 VPN/TUN，避免路由冲突。
5. 检查客户端最终生效配置：TUN、DNS 接管和 IPv6 未被全局覆写关闭，代理端口按需要与 GUI 设置保持一致。

已测试的核心组合为 **Xray 26.10.21 + Mihomo 1.19.33**。导出配置显式开启 `reality-opts.support-x25519mlkem768` 以兼容新版 REALITY；如果 GUI 内置核心较旧，请更新核心后连接。配置语法检查通过不能替代实际握手验证。

导出文件是 **YAML 1.2 兼容的 JSON**，扩展名为 `.yaml`，Mihomo 可以读取。客户端仅含公钥与访问凭据，不含服务端私钥；文件仍可被用于访问节点，必须保密。

规则顺序为局域网/本地名称直连 → 国内域名直连 → 国内 IP 直连 → 其余走日本。国内域名/IP 使用 MetaCubeX 的 `cn.mrs` 规则集，每天更新，规则下载通过日本节点。首次启动需要成功下载规则，请查看客户端日志；规则未就绪时不要将分流验收视为通过。

DNS：国内域名用阿里 DoH，其他查询用经日本节点的 Cloudflare DoH；TUN 劫持普通 53 端口 DNS。浏览器或应用自带 DoH、代理排除项、客户端全局覆写需要另行验证。默认开启 IPv6，必须确认客户端实际接管 IPv6；无法接管时可临时关闭对应网卡 IPv6，记住恢复原设置。

导出的 DNS 配置忽略 Windows 系统 hosts，防止其中的回环地址映射被当作远端地址交给 VPS；不会修改系统 hosts 文件。需要自定义解析时，应在客户端配置中显式维护。服务端使用 VLESS 中的原始目的地址进行私网拦截，关闭额外的入站域名嗅探，避免公开 SNI 掩盖原始私网地址。

这套配置没有系统级“断网保护”：日本节点不可用且 TUN 仍运行时，走节点的连接应失败；退出客户端或停用 TUN 后，系统可能恢复直连。

## 管理命令

```bash
sudo ncloud init --server YOUR_PUBLIC_IPV4  # 重复执行复用 UUID、密钥、short ID、已选端口
sudo ncloud init --port 8443               # 修改端口后重新导出配置，旧防火墙规则手动清理
sudo ncloud start
sudo ncloud stop
sudo ncloud status                        # 状态信息不包含节点凭据
sudo ncloud doctor                        # 服务、校验和、配置、监听、目标 TLS、NTP
sudo ncloud export --output /root/client-new.yaml
sudo ncloud update                        # 更新到项目清单中的推荐 Xray 版本
sudo ncloud uninstall                     # 移除服务/核心，保留身份、管理程序和备份
sudo ncloud uninstall --purge             # 同时删除当前配置；备份仍保留凭据
```

所有 Linux 管理命令均需 root。`export --state PATH --output NEW_FILE` 也可在开发机读取自有私密状态文件；它拒绝覆盖已有文件。Windows 的 chmod 不等于 NTFS ACL，请自行限制私密文件的 Windows 访问权限。

管理程序升级：在 Git checkout 中 `git pull --ff-only` 后重新 `sudo bash install.sh`；核心升级使用 `sudo ncloud update`。`--xray-version` 只接受仓库中经过审查的版本，不能使用任意 URL 安装未知二进制。

### 路径与回滚

- `/opt/ncloud`：Python 管理程序；`/usr/local/bin/ncloud`：入口。
- `/usr/local/libexec/ncloud/xray`：核心，使用专用 `ncloud` 系统账户运行。
- `/etc/ncloud/state.json`：root 0600，包含私钥和节点身份。
- `/etc/ncloud/xray.json`：root:ncloud 0640，仅服务账户可读。
- `/etc/systemd/system/ncloud-xray.service`：独立服务，不接管其他 Xray 服务。
- `/var/backups/ncloud/`：每次安装/更新前的私密快照，root 0700。

新配置先通过 Xray 校验，再替换文件并启动。启动失败、校验失败或请求的防火墙操作失败时恢复原文件与原服务状态；如果恢复也失败，返回错误及备份路径。安装器保留上一份管理程序直至新程序安装成功。突然断电或 `kill -9` 不具备自动恢复保证，需检查快照后手动恢复。

查看日志：

```bash
sudo journalctl -u ncloud-xray.service -n 80 --no-pager
sudo ss -lntp
```

诊断不会宣称云安全组、当前中国大陆网络可用性或分流已验证；这些必须从 Windows 实测。换 IP、网络或目标站点变化时需要重新测试。卸载保留防火墙规则、服务账户、备份和管理程序，避免误删共享设置；不再使用时应自行清理确认无用的资源。

## 开发与测试

```powershell
cd D:\download\ncloud
python -m unittest discover -v
python -m ncloud --help
```

Linux 上额外执行 `bash -n install.sh`。GitHub Actions 在 Windows/Linux、Python 3.8/3.11/3.13 上运行离线测试。

官方核心的真实配置/协议联调（显式联网，下载的二进制必须先核验 SHA-256）：

```powershell
python tools/verify_cores.py --xray work/xray/xray.exe --mihomo work/mihomo/mihomo.exe --network
```

联调使用临时生成的身份，在回环地址启动服务端和客户端，通过 HTTP CONNECT 检查 HTTPS 请求，并测试错误凭据不能连接。测试不安装系统服务，不启用 TUN、不修改 Windows 路由。它不能替代 Alibaba Cloud Linux 上的 systemd/SELinux 验收或真实日本出口测试。

部署后可对实际导出的私密配置进行端到端验收：

```powershell
python tools/verify_vps.py --profile exports/ncloud-client.yaml --mihomo work/mihomo/mihomo.exe
```

该检查使用完整 DNS/分流规则，在临时目录运行独立客户端，验证规则下载、国外 HTTPS 出口 IP、国内域名直连、DNS 查询和日本 IP 归属；不会启用 TUN 或改动系统路由。IP 归属依赖第三方数据库，不能替代云平台的实例地域信息。

`profiles/ncloud-client.yaml` 是公开分发的共享配置。其他导出文件、`work/`、真实状态、私钥、SSH 凭据、日志和下载的核心应保留在本机。MIT 许可仅适用于本项目；Xray 和 Mihomo 使用各自上游许可。
