# VPN 通道管理器 — 开发指南

> 给在本仓库继续开发的人/Agent。完整设计意图见 **《VPN通道管理器-落地方案.md》**;本文件是代码现状 + 命门 + 下一步的速查。

## 这是什么

单人自用、跑在本机的可视化 VPN 通道管理器。每家客户的企业 VPN(EasyConnect / aTrust)各关进一个 Docker 容器,每个容器暴露一个 SOCKS5 出口;一个**独立的第二个 mihomo** 实例按域名 / IP 分流;用户**现有的 Clash 一字不改**,只加一个 `vpn-router` 节点 + 订阅一份分流规则。全程全 Docker,本机零新增依赖。

## 仓库结构

```
test_vpn/
├── VPN通道管理器-落地方案.md      # 完整设计文档(意图的源头,先读它)
├── CLAUDE.md                       # 本文件
└── vpn-manager/
    ├── docker-compose.yml          # mihomo + app 两个服务;端口全绑 127.0.0.1
    ├── start.sh                    # 一键启动:gen_env → 渲染 mihomo 配置 → compose up
    ├── gen_env.py                  # 生成 .env(随机高位端口 + mihomo 密钥)
    ├── mihomo/
    │   ├── config.template.yaml    # mihomo 初始配置模板(__SECRET__ 占位)— 已入库
    │   └── config.yaml             # 渲染后的运行配置(含密钥+通道)— 已 gitignore
    └── app/                        # FastAPI 后端
        ├── main.py                 # 路由 + 末尾的静态前端挂载
        ├── manager.py              # Docker 编排 + mihomo 热加载 + SOCKS5 探活
        ├── store.py                # SQLite + Fernet 凭据加密
        ├── static/                 # 前端(= 现在这套 5 屏原型)
        ├── Dockerfile
        └── requirements.txt
```

## 前端现状:设计基线,尚未接后端

`app/static/` 现在是一套 **5 屏高保真原型**(设计系统 **Neutral Modern**:浅色、钴蓝 `#2F6FEB`、Inter),共享 `css/app.css` + `js/app.js` + `js/data.js`。由 FastAPI 通过 `main.py` 末尾的 `app.mount("/", StaticFiles(..., html=True))` 根挂载提供服务(`/api/*` 路由先注册、优先匹配,不受影响)。

> **关键:`js/data.js` 是 mock 假数据。每个交互(起容器 / 探活 / noVNC 登录 / 流量图)都是前端仿真,没有接后端。** 后续开发的核心任务 = 把这些屏接到 `app/` 的真实 API。

| 文件 | 屏 | 职责 |
|---|---|---|
| `index.html` | 通道总览 | 概览统计 + 通道卡片(状态徽章 / 端口 / 延迟 / 规则)、内联绑定、检测连通、删除 |
| `new-channel.html` | 新建向导 | 5 步映射状态机:填表 → 起容器 → 登录 → 探活 → 绑规则 |
| `channel.html` | 通道详情 | 概览 / 登录 / 分流规则 / 日志 四 tab;仿 noVNC 登录页;「重新登录」做成一等公民(应对设备重绑) |
| `monitor.html` | 流量监控 | 实时上下行 / 面积图 / 节点延迟 / 实时连接表(全 mock) |
| `clash-config.html` | Clash 接入 | 数据流图 + 三步接入(粘节点 / 粘规则 / 订阅) + 一键复制 |

> **接线参考:上一版可用 demo 留在 git 历史 commit 1。** 用 `git show HEAD~1:vpn-manager/app/static/index.html` 查看——它有完整的真实 `fetch()` 调用(见下表),是把新原型接后端时的最佳样板。

## 真实后端 API(以 `app/main.py` 为准)

旧 demo 的调用辅助:`fetch(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(...)})`,`setInterval(load, 8000)` 轮询刷新。

| 方法 | 路径 | 入参 / 出参 |
|---|---|---|
| GET | `/api/channels` | → 通道数组(每条含 `domains[]`) |
| POST | `/api/channels` | `{name,vpn_type,server,ec_ver,login_method,username,password,probe_url}` → 起容器并返回通道 |
| GET | `/api/channels/{cid}/login` | → `{url}`(noVNC vnc.html 地址,前端 iframe 它) |
| GET | `/api/channels/{cid}/status` | → `{status,connected}`(**会实际跑 SOCKS5 探活**) |
| POST | `/api/channels/{cid}/domains` | `{pattern}` → `{reload_status,domains}`(**一次一个域名**) |
| POST | `/api/channels/{cid}/start` \| `/stop` | → `{ok}` |
| DELETE | `/api/channels/{cid}` | → `{ok}` |
| GET | `/api/clash-snippet` | → text/plain(给用户 Clash 粘的节点 + 规则) |

数据模型(`store.py`):`channels` 表(id, name, vpn_type, server, ec_ver, login_method, username, **password_enc**[Fernet], vnc_password, mac, novnc_port, probe_url, status, container_id)+ `domains` 表(id, channel_id, pattern)。**没有 IP 表。**

## 状态机(后端 canonical)

`creating → running → logged_in`,外加 `stopped`、`error`。
- `running` = 容器起来了但还没登录成功(待登录)。
- `logged_in` = 探活通过(真连上内网)。
- 原型 UI 里多了 `starting / created / down` 等装饰态,接线时统一收敛到后端这套。

## 命门(开发中绝不能破坏)

1. **登录成功的唯一判据 = 后端 SOCKS5 探活**(`manager.probe`:经 `socks5h://vpn-{id}:1080` 访问 `probe_url`,`socks5h` = 远程解析)。**绝不能用「VNC 连上了」判定登录成功**(跨源读不到 VNC 事件)。
2. **DNS 在 VPN 侧解析**:外层用户 Clash 的规则带 `no-resolve`(不解析、直接把域名交给 `vpn-router`);内层本工具 mihomo 靠 sniffer / respect-rules 还原域名。这是 `rebuild()` 里 `DOMAIN-SUFFIX` 规则不带 `no-resolve` 也能命中的原因。
3. **配置热加载、绝不断连**:`manager.rebuild()` 重写 mihomo 配置后 `PUT {CTRL}/configs?force=true`,不重启 mihomo、不断现有连接。
4. **所有 host 端口只绑 `127.0.0.1`**(compose + manager 均如此),永不 `0.0.0.0`。
5. **凭据安全**:密码 Fernet 加密落库(`store.py`),`_row()` 永不把 `password_enc` 回传前端;`master.key` 权限 0600,存在数据卷里(容器内读不到 macOS 钥匙串)。
6. **容器细节**:SOCKS5(1080)只在 Docker 内网暴露;noVNC(8080)映射到 `127.0.0.1` 随机高位端口。aTrust 容器需 `sysctl net.ipv4.conf.default.route_localnet=1` + 环境变量 `DISABLE_PKG_VERSION_XML=1`;EasyConnect 镜像 tag 用 `ec_ver`。
7. **代理命名**:外层用户 Clash 里那个节点叫 `vpn-router`(= 整个 mihomo 实例的分流端口);内层 mihomo 里每条通道是 `ch-{id}` 的 socks5 代理。别混淆。

## 原型领先于后端 — 接线缺口 / 下一步

原型已经画出「目标设计」,但后端还没跟上,这些是明确的开发任务:

1. **IP / CIDR 绑定**:原型支持(详情 / 总览 / 向导,生成 `IP-CIDR,...,no-resolve`),**后端没有**——`domains` 表只有 `pattern`、`POST /domains` 只收单个域名、`rebuild()` 只发 `DOMAIN-SUFFIX,...,ch-{id}`。要接需:加 IP 存储(新表或给 domains 加规则类型字段)、`rebuild()` 增发 `IP-CIDR,{cidr},ch-{id},no-resolve`、`clash-snippet` 增发 IP 规则。
2. **多域名批量绑定**:原型一次可粘多条;后端 `/domains` 一次一个 `pattern`。前端循环调用,或后端加批量端点。
3. **Clash 接入形态**:原型 clash-config 屏展示的是 `behavior: classical` 的 rule-provider(一份 `vpn-rules.yaml` 同装域名 + IP);后端 `/api/clash-snippet` 目前发的是内联 `DOMAIN-SUFFIX,...,vpn-router,no-resolve`(无 provider 文件)。需决定:升级端点产出 rule-provider,还是保持内联。(这是设计阶段悬而未决的取舍。)
4. **接屏**:把 `js/data.js` 的 mock 换成 `fetch()` 调真实 API;给状态 / 日志 / 流量加轮询或 WebSocket(monitor 屏全是 mock;真实数据源是 mihomo 控制台 API 的 `/connections` 与 `/traffic` WS)。
5. **noVNC**:详情屏现在是仿真登录页;真实流程 = `GET /api/channels/{cid}/login` 拿 url → iframe 进真实 vnc.html。

## 如何跑

- **全栈(需 Docker)**:`cd vpn-manager && ./start.sh` → 管理界面 `http://127.0.0.1:${UI_PORT}`(端口见 `.env`)。首次会拉镜像。停止 `docker compose down`。
- **只看前端(改设计,不需后端 / Docker)**:`cd vpn-manager/app/static && python3 -m http.server 8080` → `http://127.0.0.1:8080`。
- `.env` / `mihomo/config.yaml` / `mihomo/cache.db` 已 gitignore(密钥 / 运行态);`.env` 缺失时 `gen_env.py` 重新生成。
- **后端依赖只在 Docker 镜像里**(系统 python 没装 fastapi),所以本机无法直接 `uvicorn` 起 app,要起就走 compose。

## 设计系统(前端保持一致)

Neutral Modern:浅色干净、钴蓝 `#2F6FEB` 点缀(**每屏至多一处强调**)、Inter(sans 作 display)、B2B 工具 / 操作台风格(不是落地页)。所有 token 在 `css/app.css` 的 `:root`;别在别处写裸 hex;不要 AI-slop(紫色渐变、emoji 图标、左边框卡片、给每个标题配图标等)。
