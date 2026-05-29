# VPN 通道管理器 — 原型接后端 + 自动化测试 设计

- 日期:2026-05-30
- 状态:已与用户确认,待出实施计划
- 范围:把现有 5 屏高保真原型(目前全 `window.VPN` mock)接到真实后端,补齐后端缺口(IP/CIDR、规则启停、rule-provider、系统信息、日志、连接遥测),并建立自动化测试。

## 1. 背景与目标

仓库现状:`app/static/` 是 5 屏原型(设计基线),交互全为前端仿真;`app/`(main/manager/store)是可用后端,但落后于原型。本设计把原型接成真,并补后端缺口。

**成功判据(可验证):**
- pytest 全绿:`store`(规则 CRUD/启停/Fernet/迁移)、`rebuild()` 配置生成、`clash` 片段与 provider、路由(FastAPI TestClient,mock docker+requests)。
- Docker 栈冒烟脚本通过:`compose up` 后栈起、`/api/system`·`/api/channels`·`/clash/vpn-rules.yaml` 响应 200、`/clash/vpn-rules.yaml` 为合法 YAML、mihomo `PUT /configs?force=true` 成功(热加载不重启)。
- 5 屏全部走 `fetch` 调真实 API,无 `window.VPN` mock 残留(删除 `js/data.js` 及其引用)。
- 命门不破(见 §8)。

## 2. 接入架构决策(Clash)

**核心两层路由**(整个设计的关键分工):
- **外层用户 Clash**:只判断「这是不是要走 VPN 的目标」→ 命中一律丢给一个 `vpn-router` 节点。Clash 不需要知道有几条通道。
- **内层本工具 mihomo**:决定「走哪条通道」,按 `ch-{id}` 规则分到对应容器 SOCKS5。

用户环境已确认:**Clash 长期开启、TUN 模式**。TUN 只与「把本工具放最前」冲突,与本方案(Clash 加节点+规则)**不冲突**:TUN 抓全量,Clash 规则引擎决策,我们只是新增一个节点 + 一份订阅。

支持两种模式,共享同一核心:

### 模式 A — 有 Clash(用户主用,TUN 友好)
用户一次性粘贴:
```yaml
proxies:
  - name: vpn-router
    type: socks5
    server: 127.0.0.1
    port: 48721            # = MIHOMO_HOST_PORT,本工具 mihomo mixed-port
rule-providers:
  vpn-rules:
    type: http
    behavior: classical    # 一份清单同装域名 + IP
    format: yaml
    url: http://127.0.0.1:42411/clash/vpn-rules.yaml   # = 本工具 UI 源,前端用 location.origin 派生
    interval: 3600
    path: ./providers/vpn-rules.yaml
rules:
  - RULE-SET,vpn-rules,vpn-router,no-resolve           # 放 rules 顶部
```
之后每次在本工具绑新域名/IP → provider 内容即变 → Clash 按 interval 自动重拉,**永不再动 Clash**。

### 模式 B — 无 Clash(任意机器,零后端改动)
系统/浏览器代理指向 `127.0.0.1:48721`。本工具 mihomo 自身 `mode: rule` + 末位 `MATCH,DIRECT`(config.template.yaml 已是),命中→VPN 容器,其余→直连。接入屏作为第二个 tab 写清楚。

### 链路(TUN 下)
- **内网域名** `crm.weidu.内网`:TUN 抓包 → Clash `DOMAIN-SUFFIX` 命中(域名按名匹配、不本地解析)→ `vpn-router`(我们 mihomo :48721)→ 内层 `DOMAIN-SUFFIX,...,ch-{id}` 命中 → `ch-{id}` SOCKS5(`socks5h` 远程解析)→ 容器在 VPN 侧解析出网。
- **无域名内网 IP** `10.20.4.12`:TUN 见真实目的 IP → Clash `IP-CIDR,10.20.0.0/16` 命中(`no-resolve` 只按目的 IP 匹配)→ `vpn-router` → 内层 `IP-CIDR,...,ch-{id},no-resolve` → 容器出网。
- **其余一切**:不命中 RULE-SET,照用户现有 Clash/TUN 走,不受影响。

### 诚实前提
- **TUN 建议 fake-ip 模式**(常见默认):内网域名才能「按名匹配、交 vpn-router、VPN 侧解析」。若 TUN 为 redir-host 且本地预解析内网域名,域名分流受影响(IP 分流不受影响)。接入屏标注,接好后实测确认。
- **provider 拉取需本工具在线**:app 为 `restart:unless-stopped` 常驻;app 挂时 Clash 用本地缓存 `./providers/vpn-rules.yaml`,已有分流照常,仅新绑暂不同步。

> `no-resolve` 说明:对清单内 `IP-CIDR` 行生效(只按目的 IP 匹配、不解析);对 `DOMAIN-SUFFIX` 行为无操作但无害。内网域名「不本地解析」靠的是它是域名规则、按名匹配后把域名直接交给 vpn-router(命门#2)。

## 3. 数据模型变更(store.py)

统一 `domains` → 一张 `rules` 表:
```sql
CREATE TABLE IF NOT EXISTS rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id TEXT,
  kind TEXT,          -- 'domain' | 'ip'
  pattern TEXT,
  enabled INTEGER DEFAULT 1
);
```
- **迁移**:`init()` 中若 `rules` 为空且 `domains` 有行,复制 `domains` → `rules`(kind='domain', enabled=1)。保留旧 `domains` 表(不再读写)。
- `channels` 表加列 `latency_ms INTEGER`(ALTER TABLE if missing),由 status 探活更新。
- 新增 store 函数:`add_rule(cid, kind, pattern)`、`list_rules(cid)`、`all_rules()`、`del_rule(rid)`、`set_rule_enabled(rid, enabled)`、`set_latency(cid, ms)`。
- `_row()` 仍永不回传 `password_enc`。

## 4. 后端 API 变更(main.py / manager.py)

| 方法 | 路径 | 状态 | 契约 |
|---|---|---|---|
| GET | `/api/channels` | 改 | 每条增:`domains[]`/`ips[]`(各 `{id,pattern,enabled}`,来自 rules)、`latency_ms`、`uptime`(docker inspect StartedAt 派生)、`volume_name`=`vpndata-{id}`、`socks_proxy`=`ch-{id}`、`socks_endpoint`=`vpn-{id}:1080` |
| GET | `/api/system` | 新 | `{mihomo_status:"running"\|"down", mihomo_port, controller, ui_port, bound_ip}`;mihomo_status 由 ping 控制台 `/version` 判定 |
| POST | `/api/channels/{cid}/rules` | 新 | 入 `{patterns:[...], kind?:"domain"\|"ip"}`;无 kind 时服务端自动识别(IP 用 `ipaddress` 校验+规范化为 CIDR,否则域名);出 `{reload_status, domains[], ips[], added:{domain,ip}, rejected:[]}` |
| DELETE | `/api/channels/{cid}/rules/{rid}` | 新 | → `{ok, reload_status}` |
| PATCH | `/api/channels/{cid}/rules/{rid}` | 新 | 入 `{enabled:bool}` → `{ok, reload_status}` |
| GET | `/api/channels/{cid}/status` | 改 | → `{status, connected, latency_ms}`(probe 计时) |
| GET | `/api/channels/{cid}/logs?tail=200` | 新 | → `{lines:[...]}`(container.logs(tail=) 解码切行) |
| GET | `/api/connections` | 新 | 代理 mihomo `GET /connections`(带 secret),原样/裁剪回传 |
| GET | `/clash/vpn-rules.yaml` | 新 | `text/yaml`,classical payload,仅 enabled 规则,全通道合并 |
| GET | `/api/clash-snippet` | 改 | 节点 + 内联(补 IP-CIDR,no-resolve)+ provider 引用注释 + 无 Clash 模式说明 |
| POST | `/api/channels` | 微调 | 维持原入参;为 EC 账密路径接收 `password`(已支持) |
| GET | `/api/channels/{cid}/login`,POST `/start`·`/stop`,DELETE `/{cid}` | 不变 | — |

**manager.py:**
- `rebuild()`:对每条 enabled 规则,domain → `DOMAIN-SUFFIX,{pattern},ch-{id}`,ip → `IP-CIDR,{cidr},ch-{id},no-resolve`;`MATCH,DIRECT` 末位;`PUT /configs?force=true` 热加载(不变)。
- `probe()`:返回 `(ok:bool, latency_ms:int|None)`,计时 `requests.get` 往返。
- 新增 `uptime(cid)`:docker inspect `State.StartedAt` → "Xd Yh"/"Ym";`logs(cid, tail)`;`connections()`:GET mihomo `/connections`。

**`/clash/vpn-rules.yaml` 内容**:
```yaml
payload:
  - DOMAIN-SUFFIX,{域名去掉 +. / *. 前缀}
  - IP-CIDR,{cidr}
```

**compose 微调**:app service env 增 `UI_PORT: ${UI_PORT}`、`MIHOMO_CTRL_PORT: ${MIHOMO_CTRL_PORT}`(供 `/api/system` 报告端口;前端亦可用 `location` 派生 ui_port/bound_ip 作兜底)。

## 5. 前端五屏接线(js/data.js mock → fetch)

通用:删除 `js/data.js` 及五处 `<script src="js/data.js">`;新增轻量 `js/api.js` 封装 `fetch` + 错误 toast。**状态显示一律以后端 canonical 五态为准**(`creating/running/logged_in/stopped/error`);原型装饰态(`created/starting/down`)降级为**纯本地瞬时态**——仅在请求在途期间做加载动效(如起容器/重启时的「启动中」spinner),请求返回后立即渲染后端真实状态,不落库、不作判据。

| 屏 | 接线 |
|---|---|
| index | `GET /api/channels`+`/api/system` 渲染;检测连通→`GET /status`(显示真延迟);快绑→`POST /rules`;删→`DELETE /{cid}`;8s 轮询刷新 |
| new-channel | step2 起容器→`POST /api/channels`(用 step1 表单);step3 登录→iframe 真 `GET /login` 的 url;step4 探活→`GET /status`;step5 绑规则→`POST /rules`;**补密码输入**(EC 账密方式,run 预览的 `-p` 才有来源) |
| channel | 概览/健康/日志填真(`/logs` 真实 docker 日志);登录 tab→真 noVNC iframe(`GET /login`);规则表增/删/启停→`POST`·`DELETE`·`PATCH /rules`;启停/重启→`/start`·`/stop`;删→`DELETE` |
| monitor | `GET /api/connections` 轮询(~1.5s):连接表(host/rule/chain→通道/上下行)、上下行速率(由 downloadTotal/uploadTotal 差分)、连接数;节点延迟取各通道 `latency_ms`;活动节点=logged_in 数;移除纯随机仿真 |
| clash-config | 双模式:模式 A(节点 + provider 订阅,**主推**;`/clash/vpn-rules.yaml` URL 用 `location.origin` 派生)+ 模式 B(指系统代理到 `:mihomo_port`);三段片段由 `/api/channels`+`/api/system`+`location` 客户端实时拼,保留一键复制 |

## 6. 真相对齐(守命门的小修)

- **SOCKS 不映射 host**(命门#6):前端把原型编造的 `:10800` 改为显示内网出口 `vpn-{id}:1080` / 代理名 `ch-{id}`,不再展示伪 host 端口。
- **卷名**对齐真实 `vpndata-{id}`(原型写 `vpn-{id}-root` 不符 manager.py)。
- **状态机**:前端装饰态收敛到后端 canonical 五态。

## 7. 测试策略

### 7.1 后端 pytest(本机 venv,mock `docker`+`requests`)
本机系统 Python 缺 fastapi/uvicorn/docker/requests;建 `tests/` + venv,`pip install fastapi uvicorn requests docker pytest httpx pyyaml cryptography`。已具:pytest 8.4、httpx、yaml、cryptography。
- `test_store.py`:rules 增删/启停、Fernet 加解密、`_row` 隐藏 password_enc、domains→rules 迁移、latency 持久化。
- `test_rebuild.py`:`rebuild()` 生成正确 proxies + `DOMAIN-SUFFIX,...,ch-{id}` + `IP-CIDR,...,ch-{id},no-resolve`、enabled 过滤、`MATCH,DIRECT` 末位(mock `requests.put`,断言写入 YAML)。
- `test_clash.py`:`/clash/vpn-rules.yaml` classical payload(仅 enabled、域名去前缀、IP 行)、`/api/clash-snippet` 含节点+IP+provider 引用。
- `test_api.py`:FastAPI TestClient,mock `manager` 的 docker/requests:create→list(rules 回显)、rules POST 批量+自动识别+拒非法 IP、DELETE/PATCH、`/api/system`、`/login` url、`/status` 延迟、`/logs`。

### 7.2 Docker 栈冒烟(`tests/smoke.sh`)
`docker compose up -d` → 等就绪 → 断言:`/api/system` mihomo running、`/api/channels` 200、`/clash/vpn-rules.yaml` 合法 YAML、mihomo `PUT /configs` 成功(可用 store 直插一条假通道触发 rebuild 验证热加载)。
**不含真 VPN 登录**(无真实企业 VPN);真容器 create(拉 EC/aTrust 镜像)与真登录标注为人工验证,不进自动化(镜像大、需真凭据)。

## 8. 命门检查清单(实现中逐条守)
1. 登录成功唯一判据 = 后端 SOCKS5 探活(不用 VNC 连上判定)。
2. Clash 规则带 `no-resolve`;内网域名 VPN 侧解析。
3. `rebuild()` 热加载 `PUT ?force=true`,不重启 mihomo、不断连。
4. 所有 host 端口绑 `127.0.0.1`。
5. 密码 Fernet 落库,`_row` 不回传 `password_enc`。
6. SOCKS5(1080)只在 Docker 内网;noVNC 映射 127.0.0.1 随机高位端口。
7. 命名:外层 `vpn-router`,内层 `ch-{id}`。

## 9. 执行编排(workflow)
- **阶段 1 实现后端**(store+manager+main 紧耦合,作一个连贯单元):rules 表+迁移、新增/改路由、rebuild IP 支持、probe 计时、system/logs/connections/provider 端点、compose env 微调。
- **阶段 2 并行**(文件互不冲突):5 屏接线各一 agent(index/new-channel/channel/monitor/clash-config)+ `js/api.js` + 4 套 pytest + `smoke.sh`。
- **阶段 3 验证**:建 venv 跑 pytest、跑 Docker 冒烟、对抗式复审(命门清单 + 契约一致性)。

## 10. 范围边界
- 不做:真 VPN 登录自动化、aTrust 免验证码、增量 rule-provider(整份热加载即可)、React 重写。
- 监控流量图由轮询 `/connections` 差分得到(非 WebSocket),足够「实时感」且实现简单。
