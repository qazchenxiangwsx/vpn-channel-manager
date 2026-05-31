# VPN 通道管理器

> 单人自用、跑在本机的可视化 VPN 通道管理器。把每家客户的企业 VPN(EasyConnect / aTrust)各关进一个 Docker 容器,每个容器暴露一个 SOCKS5 出口;一个独立的第二个 mihomo 实例按域名 / IP 分流;现有 Clash 一字不改,只加一个 `vpn-router` 节点 + 订阅一份分流规则。全程全 Docker,本机零新增依赖。

## 解决什么问题

要同时连多家客户的企业 VPN,官方客户端会互相抢路由 / DNS、各装一堆驱动,而且通常一次只能连一家。这个工具把每家 VPN 隔离进各自的容器,对外统一成 SOCKS5 出口,再按域名 / IP 分流——多家内网同时可达,互不干扰。

## 架构

三层,流量自外向内:

```
你现有的 Clash ──(命中分流规则)──▶ vpn-router 节点
                                        │
              第二个 mihomo(本工具)── 按 域名 / IP 分流 ──▶ ch-1 / ch-2 / …
                                        │
        每家 VPN 一个容器(ch-{id})── EasyConnect / aTrust ──▶ SOCKS5 出口 ──▶ 客户内网
```

- **登录**:每个容器跑 noVNC,在浏览器里完成企业 VPN 的交互式登录(支持「重新登录」应对设备重绑)。
- **判活**:后端经 SOCKS5 探活(`socks5h` 远程解析 `probe_url`)判定是否真连上内网——而非「VNC 连上了」。
- **热加载**:加 / 改分流规则后,mihomo 配置热重载,**不断现有连接**。

## 快速开始

需要 Docker。

```bash
cd vpn-manager
./start.sh
```

`start.sh` 会:生成 `.env`(随机高位端口 + mihomo 密钥)→ 渲染 mihomo 配置 → `docker compose up`。
启动后管理界面在 `http://127.0.0.1:${UI_PORT}`(端口见 `vpn-manager/.env`)。首次会拉镜像。停止:`docker compose down`。

只改前端、不想起后端:

```bash
cd vpn-manager/app/static
python3 -m http.server 8080
```

## 技术栈

- **后端**:Python + FastAPI / uvicorn;docker SDK 编排容器;cryptography(Fernet)加密凭据;SQLite 落库
- **分流**:mihomo(`metacubex/mihomo`)第二实例,经控制台 API 热加载,不重启、不断连
- **VPN 容器**:`hagb/docker-easyconnect`、`hagb/docker-atrust`(noVNC 登录 + SOCKS5 出口)
- **前端**:纯静态 HTML / CSS / JS(无框架),5 屏 —— 总览 / 新建向导 / 通道详情 / 流量监控 / Clash 接入

## 仓库结构

```
test_vpn/
├── README.md                       # 本文件
├── CLAUDE.md                       # 开发速查(代码现状 / 命门 / 下一步)
├── VPN通道管理器-落地方案.md         # 完整设计文档(意图源头)
└── vpn-manager/
    ├── docker-compose.yml          # mihomo + app 两服务,端口全绑 127.0.0.1
    ├── start.sh                    # 一键启动
    ├── gen_env.py                  # 生成 .env(随机端口 + 密钥)
    ├── mihomo/
    │   └── config.template.yaml    # mihomo 配置模板
    └── app/                        # FastAPI 后端
        ├── main.py                 # 路由 + 静态前端挂载
        ├── manager.py              # Docker 编排 + mihomo 热加载 + SOCKS5 探活
        ├── store.py                # SQLite + Fernet 凭据加密
        ├── static/                 # 5 屏前端
        ├── Dockerfile
        └── requirements.txt
```

## HTTP API

以 `vpn-manager/app/main.py` 为准。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/channels` | 通道列表(每条含 `domains[]` / `ips[]` / `uptime` 等) |
| POST | `/api/channels` | 建通道并起容器(`name, vpn_type, server, ec_ver, login_method, username, password, probe_url`) |
| POST | `/api/channels/{cid}/start` \| `/stop` | 起 / 停容器 → `{ok}` |
| DELETE | `/api/channels/{cid}` | 删通道 → `{ok}` |
| GET | `/api/channels/{cid}/login` | noVNC 登录地址 → `{url}` |
| GET | `/api/channels/{cid}/status` | **跑 SOCKS5 探活** → `{status, connected, latency_ms}` |
| GET | `/api/channels/{cid}/logs?tail=200` | 容器日志 → `{lines}` |
| POST | `/api/channels/{cid}/rules` | 加分流规则(`patterns[]` 或 `pattern`,可选 `kind: domain\|ip`)→ `{reload_status, domains, ips, added, rejected}` |
| PATCH | `/api/channels/{cid}/rules/{rid}` | 启用 / 停用一条规则(`enabled`)→ `{ok, reload_status}` |
| DELETE | `/api/channels/{cid}/rules/{rid}` | 删规则 → `{ok, reload_status}` |
| GET | `/api/system` | mihomo 状态 / 端口 / 控制台地址 |
| GET | `/api/connections` | mihomo 实时连接 |
| GET | `/clash/vpn-rules.yaml` | 给 Clash 订阅的 rule-provider 清单(`text/plain`) |
| GET | `/api/clash-snippet` | 给用户 Clash 粘的节点 + 规则(`text/plain`) |

## 通道状态机

```
creating ──▶ running ──▶ logged_in     （另有 stopped、error）
```

- **running**:容器起来了,还没登录成功(待登录)
- **logged_in**:SOCKS5 探活通过(真连上内网)

## 安全

- 所有 host 端口只绑 `127.0.0.1`,**永不** `0.0.0.0`
- 密码 Fernet 加密落库;`master.key` 权限 0600、存在数据卷;接口永不回传密文
- SOCKS5(1080)只在 Docker 内网暴露;noVNC 映射到 `127.0.0.1` 随机高位端口

## 文档

- **CLAUDE.md** —— 代码现状 / 命门 / 下一步的开发速查
- **VPN通道管理器-落地方案.md** —— 完整设计意图(先读它)
