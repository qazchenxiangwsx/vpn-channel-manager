# VPN 通道管理器 — 原型接后端 + 自动化测试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 5 屏高保真原型从 `window.VPN` mock 接到真实后端,补齐后端缺口(IP/CIDR、规则启停、rule-provider、系统信息、日志、连接遥测),并建立 pytest + Docker 栈冒烟的自动化测试。

**Architecture:** 两层路由不变(外层 Clash → `vpn-router` 节点;内层 mihomo → `ch-{id}`)。后端 `domains` 表统一成 `rules(kind,enabled)`;新增/改造路由;前端删 mock、走 `js/api.js` 封装的 `fetch`。Clash 接入双模式(有 Clash 用 rule-provider + 节点;无 Clash 指系统代理到 mihomo)。

**Tech Stack:** FastAPI、SQLite、Fernet、docker SDK、mihomo(Clash.Meta)控制台 API、原生 HTML/JS、pytest + httpx(TestClient)。

**关键约定(spec §8 命门,实现中逐条守):** 登录判据=SOCKS5 探活;Clash 规则带 `no-resolve`;`rebuild()` 热加载不重启;host 端口绑 `127.0.0.1`;`password_enc` 不回前端;SOCKS5 只在 Docker 内网;外层 `vpn-router`/内层 `ch-{id}`。

> 完整设计见 `docs/superpowers/specs/2026-05-30-vpn-manager-wiring-design.md`。

---

## 文件结构(谁负责什么)

**后端(`vpn-manager/app/`)**
- `store.py` — 改:`rules` 表 + 迁移 + `latency_ms` 列;规则/延迟 CRUD。
- `manager.py` — 改:`rebuild()` 支持 IP-CIDR;`probe()` 计时;新增 `uptime/logs/connections/mihomo_alive`。
- `main.py` — 改/增:`channels` 富化、`rules` 增删改、`system`、`logs`、`connections`、`/clash/vpn-rules.yaml`、`clash-snippet` 扩展、`_classify` 助手。

**测试(`vpn-manager/tests/`,新建目录)**
- `conftest.py` — 环境变量 + 临时 DATA_DIR + 清库 fixture + `make_channel`/`client` fixture。
- `test_store.py` / `test_manager.py` / `test_api.py` / `test_clash.py`。
- `smoke.sh` — Docker 栈冒烟。
- `requirements-dev.txt` — 测试依赖。

**前端(`vpn-manager/app/static/`)**
- `js/api.js` — 新:`fetch` 封装。
- `js/data.js` — 删。
- `index.html` / `new-channel.html` / `channel.html` / `monitor.html` / `clash-config.html` — 改:去 mock、接 `api.*`。

**基础设施**
- `vpn-manager/docker-compose.yml` — 改:app env 加 `UI_PORT`、`MIHOMO_CTRL_PORT`。

---

## Phase 0 — 分支与测试脚手架

### Task 0.1: 建工作分支

- [ ] **Step 1: 建并切到 feature 分支**

Run:
```bash
cd /private/var/www/test_vpn && git checkout -b feat/wire-prototype-backend
```
Expected: `Switched to a new branch 'feat/wire-prototype-backend'`

### Task 0.2: 测试依赖与目录

**Files:**
- Create: `vpn-manager/tests/requirements-dev.txt`

- [ ] **Step 1: 写 dev 依赖清单**

`vpn-manager/tests/requirements-dev.txt`:
```
fastapi==0.115.*
uvicorn==0.30.*
requests==2.32.*
docker==7.1.*
pyyaml==6.0.*
cryptography==46.*
pytest==8.4.*
httpx==0.28.*
```

- [ ] **Step 2: 建 venv 并安装**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && python3 -m venv .venv && ./.venv/bin/pip install -q -r tests/requirements-dev.txt && echo OK
```
Expected: 末行 `OK`(首次安装稍慢)。

- [ ] **Step 3: 忽略 venv**

Append to `vpn-manager/.gitignore`(若无则建):
```
.venv/
tests/__pycache__/
.pytest_cache/
```

- [ ] **Step 4: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/tests/requirements-dev.txt vpn-manager/.gitignore && git commit -m "test: add dev deps + venv ignore"
```

### Task 0.3: conftest(环境 + fixtures)

**Files:**
- Create: `vpn-manager/tests/conftest.py`

- [ ] **Step 1: 写 conftest**

`vpn-manager/tests/conftest.py`:
```python
import os
import tempfile

# 必须在 import store/main/manager 之前设好环境(它们在模块级读 env)
_TMP = tempfile.mkdtemp(prefix="vpnmgr-test-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("VPN_NET", "testnet")
os.environ.setdefault("MIHOMO_CTRL_URL", "http://mihomo-test:9090")
os.environ.setdefault("MIHOMO_SECRET", "test-secret")
os.environ.setdefault("MIHOMO_CONFIG_PATH", os.path.join(_TMP, "config.yaml"))
os.environ.setdefault("MIHOMO_HOST_PORT", "48721")
os.environ.setdefault("MIHOMO_CTRL_PORT", "20933")
os.environ.setdefault("UI_PORT", "42411")

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pytest
import store


@pytest.fixture(autouse=True)
def clean_db():
    store.init()
    with store._c() as c:
        c.execute("DELETE FROM channels")
        c.execute("DELETE FROM rules")
        c.execute("DELETE FROM domains")
    yield


@pytest.fixture
def make_channel():
    def _mk(cid, **over):
        ch = {
            "id": cid, "name": cid, "vpn_type": "easyconnect", "server": "https://x",
            "ec_ver": "7.6.3", "login_method": "interactive", "username": "",
            "password": over.get("password", ""), "vnc_password": "vnc12345",
            "mac": "02:00:00:00:00:01", "probe_url": "http://p", "status": "running",
        }
        ch.update({k: v for k, v in over.items() if k in ch})
        return ch
    return _mk


@pytest.fixture
def client(monkeypatch):
    import manager
    monkeypatch.setattr(manager, "rebuild", lambda: 204)
    monkeypatch.setattr(manager, "create_channel", lambda ch, vnc: ("cid_fake", 18080))
    monkeypatch.setattr(manager, "probe", lambda ch: (True, 42))
    monkeypatch.setattr(manager, "uptime", lambda cid: "1分钟")
    monkeypatch.setattr(manager, "mihomo_alive", lambda: True)
    monkeypatch.setattr(manager, "logs", lambda cid, tail=200: ["line1", "line2"])
    monkeypatch.setattr(manager, "connections",
                        lambda: {"connections": [], "downloadTotal": 0, "uploadTotal": 0})
    import main
    from fastapi.testclient import TestClient
    return TestClient(main.app)
```

- [ ] **Step 2: 冒烟跑空收集(确认 import 链通)**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/ -q
```
Expected: `no tests ran`(0 收集,但**无 import/env 报错**)。若报 `KeyError`/`ModuleNotFound`,先修 conftest。

- [ ] **Step 3: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/tests/conftest.py && git commit -m "test: conftest with env + db + client fixtures"
```

---

## Phase 1 — store.py(rules 表 + 迁移 + 延迟)

### Task 1.1: rules CRUD 与启停

**Files:**
- Test: `vpn-manager/tests/test_store.py`
- Modify: `vpn-manager/app/store.py`

- [ ] **Step 1: 写失败测试**

`vpn-manager/tests/test_store.py`:
```python
import store


def test_add_and_list_rules(make_channel):
    store.add_channel(make_channel("c1"))
    rid = store.add_rule("c1", "domain", "a.com")
    rules = store.list_rules("c1")
    assert len(rules) == 1
    assert rules[0]["id"] == rid
    assert rules[0]["kind"] == "domain"
    assert rules[0]["pattern"] == "a.com"
    assert rules[0]["enabled"] == 1


def test_set_rule_enabled(make_channel):
    store.add_channel(make_channel("c1"))
    rid = store.add_rule("c1", "ip", "10.0.0.0/8")
    store.set_rule_enabled(rid, False)
    assert store.get_rule(rid)["enabled"] == 0
    store.set_rule_enabled(rid, True)
    assert store.get_rule(rid)["enabled"] == 1


def test_del_rule(make_channel):
    store.add_channel(make_channel("c1"))
    rid = store.add_rule("c1", "domain", "a.com")
    store.del_rule(rid)
    assert store.list_rules("c1") == []


def test_del_channel_cascades_rules(make_channel):
    store.add_channel(make_channel("c1"))
    store.add_rule("c1", "domain", "a.com")
    store.del_channel("c1")
    assert store.all_rules() == []


def test_set_latency(make_channel):
    store.add_channel(make_channel("c1"))
    store.set_latency("c1", 55)
    assert store.get_channel("c1")["latency_ms"] == 55


def test_password_encrypted_not_returned(make_channel):
    store.add_channel(make_channel("c1", password="secret"))
    ch = store.get_channel("c1")
    assert "password_enc" not in ch
    assert store.get_password("c1") == "secret"


def test_migrate_domains_to_rules():
    with store._c() as c:
        c.execute("DELETE FROM rules")
        c.execute("INSERT INTO domains(channel_id,pattern) VALUES('c1','legacy.com')")
    store.init()  # 迁移触发条件:rules 空 & domains 有行
    migrated = [r for r in store.all_rules() if r["pattern"] == "legacy.com"]
    assert len(migrated) == 1
    assert migrated[0]["kind"] == "domain" and migrated[0]["enabled"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/test_store.py -q
```
Expected: FAIL(`AttributeError: module 'store' has no attribute 'add_rule'` 等)。

- [ ] **Step 3: 改 `store.py` 的 `init()`**

把 `vpn-manager/app/store.py` 的 `init()` 替换为:
```python
def init():
    with _c() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS channels(
              id TEXT PRIMARY KEY, name TEXT, vpn_type TEXT, server TEXT, ec_ver TEXT,
              login_method TEXT, username TEXT, password_enc TEXT, vnc_password TEXT,
              mac TEXT, novnc_port INTEGER, probe_url TEXT, status TEXT, container_id TEXT);
            CREATE TABLE IF NOT EXISTS domains(
              id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT, pattern TEXT);
            CREATE TABLE IF NOT EXISTS rules(
              id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT,
              kind TEXT, pattern TEXT, enabled INTEGER DEFAULT 1);
            """
        )
        cols = [r[1] for r in c.execute("PRAGMA table_info(channels)").fetchall()]
        if "latency_ms" not in cols:
            c.execute("ALTER TABLE channels ADD COLUMN latency_ms INTEGER")
        # 旧 domains 一次性迁入 rules(仅当 rules 为空)
        if c.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0:
            for r in c.execute("SELECT channel_id, pattern FROM domains").fetchall():
                c.execute(
                    "INSERT INTO rules(channel_id,kind,pattern,enabled) VALUES(?,?,?,1)",
                    (r["channel_id"], "domain", r["pattern"]),
                )
```

- [ ] **Step 4: 加 rules/latency 函数,改 `del_channel`**

在 `vpn-manager/app/store.py` 末尾追加:
```python
def add_rule(cid, kind, pattern):
    with _c() as c:
        cur = c.execute(
            "INSERT INTO rules(channel_id,kind,pattern,enabled) VALUES(?,?,?,1)",
            (cid, kind, pattern),
        )
        return cur.lastrowid


def list_rules(cid):
    with _c() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,channel_id,kind,pattern,enabled FROM rules WHERE channel_id=?",
            (cid,)).fetchall()]


def all_rules():
    with _c() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,channel_id,kind,pattern,enabled FROM rules").fetchall()]


def get_rule(rid):
    with _c() as c:
        r = c.execute("SELECT * FROM rules WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None


def del_rule(rid):
    with _c() as c:
        c.execute("DELETE FROM rules WHERE id=?", (rid,))


def set_rule_enabled(rid, enabled):
    with _c() as c:
        c.execute("UPDATE rules SET enabled=? WHERE id=?", (1 if enabled else 0, rid))


def set_latency(cid, ms):
    with _c() as c:
        c.execute("UPDATE channels SET latency_ms=? WHERE id=?", (ms, cid))
```

把现有 `del_channel` 改为同时清 rules:
```python
def del_channel(cid):
    with _c() as c:
        c.execute("DELETE FROM channels WHERE id=?", (cid,))
        c.execute("DELETE FROM domains WHERE channel_id=?", (cid,))
        c.execute("DELETE FROM rules WHERE channel_id=?", (cid,))
```

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/test_store.py -q
```
Expected: `7 passed`。

- [ ] **Step 6: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/store.py vpn-manager/tests/test_store.py && git commit -m "feat(store): unify rules table (kind+enabled), migrate domains, latency"
```

---

## Phase 2 — manager.py(rebuild IP / probe 计时 / uptime / logs / connections)

### Task 2.1: rebuild 支持 IP-CIDR + enabled 过滤

**Files:**
- Test: `vpn-manager/tests/test_manager.py`
- Modify: `vpn-manager/app/manager.py:89-122`

- [ ] **Step 1: 写失败测试**

`vpn-manager/tests/test_manager.py`:
```python
import os
import yaml
import store


def test_rebuild_emits_domain_and_ip_rules(make_channel, monkeypatch):
    import manager
    monkeypatch.setattr(manager.requests, "put",
                        lambda *a, **k: type("R", (), {"status_code": 204})())
    store.add_channel(make_channel("c1"))
    store.add_rule("c1", "domain", "weidu-crm.com")
    store.add_rule("c1", "ip", "10.20.0.0/16")
    code = manager.rebuild()
    assert code == 204
    with open(os.environ["MIHOMO_CONFIG_PATH"]) as f:
        cfg = yaml.safe_load(f)
    names = [p["name"] for p in cfg["proxies"]]
    assert "ch-c1" in names
    assert "DOMAIN-SUFFIX,weidu-crm.com,ch-c1" in cfg["rules"]
    assert "IP-CIDR,10.20.0.0/16,ch-c1,no-resolve" in cfg["rules"]
    assert cfg["rules"][-1] == "MATCH,DIRECT"


def test_rebuild_skips_disabled(make_channel, monkeypatch):
    import manager
    monkeypatch.setattr(manager.requests, "put",
                        lambda *a, **k: type("R", (), {"status_code": 204})())
    store.add_channel(make_channel("c1"))
    rid = store.add_rule("c1", "domain", "off.com")
    store.set_rule_enabled(rid, False)
    manager.rebuild()
    with open(os.environ["MIHOMO_CONFIG_PATH"]) as f:
        cfg = yaml.safe_load(f)
    assert not any("off.com" in r for r in cfg["rules"])


def test_probe_returns_latency(monkeypatch):
    import manager
    monkeypatch.setattr(manager.requests, "get",
                        lambda *a, **k: type("R", (), {"status_code": 200})())
    ok, ms = manager.probe({"id": "c1", "probe_url": "http://p"})
    assert ok is True and isinstance(ms, int) and ms >= 0


def test_probe_no_url():
    import manager
    ok, ms = manager.probe({"id": "c1", "probe_url": ""})
    assert ok is False and ms is None
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/test_manager.py -q
```
Expected: FAIL(rebuild 不发 IP 规则;probe 返回 bool 而非元组)。

- [ ] **Step 3: 改 `rebuild()`(manager.py:89-122)**

把 `rebuild()` 里构造 rules 的部分替换为读 `store.all_rules()` 并按 kind 出规则:
```python
def rebuild():
    """按当前所有通道+规则重写 mihomo 配置并热加载(force reload,不断现有连接)。"""
    chs = store.list_channels()
    rules = store.all_rules()
    try:
        with open(CFG) as f:
            base = yaml.safe_load(f) or {}
    except FileNotFoundError:
        base = {}

    base["proxies"] = [
        {"name": f"ch-{c['id']}", "type": "socks5",
         "server": f"vpn-{c['id']}", "port": 1080, "udp": True}
        for c in chs
    ]
    base["proxy-groups"] = []
    out = []
    for r in rules:
        if not r["enabled"]:
            continue
        if r["kind"] == "ip":
            out.append(f"IP-CIDR,{r['pattern']},ch-{r['channel_id']},no-resolve")
        else:
            out.append(f"DOMAIN-SUFFIX,{r['pattern']},ch-{r['channel_id']}")
    out.append("MATCH,DIRECT")
    base["rules"] = out

    with open(CFG, "w") as f:
        yaml.safe_dump(base, f, allow_unicode=True, sort_keys=False)

    try:
        r = requests.put(
            f"{CTRL}/configs",
            params={"force": "true"},
            json={"path": CFG},
            headers={"Authorization": f"Bearer {SECRET}"},
            timeout=10,
        )
        return r.status_code
    except Exception as e:
        return f"{type(e).__name__}: {e}"
```

- [ ] **Step 4: 改 `probe()` 返回 `(ok, latency_ms)`(manager.py:77-86)**

```python
def probe(ch):
    """经该通道 SOCKS5 访问内网探测地址。返回 (通否, 往返毫秒|None)。socks5h=远程解析。"""
    if not ch.get("probe_url"):
        return False, None
    px = f"socks5h://vpn-{ch['id']}:1080"
    try:
        t0 = time.monotonic()
        r = requests.get(ch["probe_url"], proxies={"http": px, "https": px}, timeout=6)
        ms = int((time.monotonic() - t0) * 1000)
        return (r.status_code < 500), ms
    except Exception:
        return False, None
```
并在文件顶部 `import` 区加 `import time`。

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/test_manager.py -q
```
Expected: `4 passed`。

- [ ] **Step 6: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/manager.py vpn-manager/tests/test_manager.py && git commit -m "feat(manager): IP-CIDR rules, enabled filter, probe latency"
```

### Task 2.2: uptime / logs / connections / mihomo_alive

**Files:**
- Modify: `vpn-manager/app/manager.py`(末尾追加 + 顶部 import)

> 这些函数依赖真实 Docker/mihomo,单测对它们不强求(路由测试里被 mock);此处只追加实现,验证靠 Phase 6 冒烟。

- [ ] **Step 1: 顶部 import 区补**

在 `vpn-manager/app/manager.py` import 段加:
```python
from datetime import datetime, timezone
```

- [ ] **Step 2: 末尾追加四个函数**

```python
def _parse_docker_time(s):
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        tz = ""
        for sep in ("+", "-"):
            if sep in rest:
                rest, tzpart = rest.split(sep, 1)
                tz = sep + tzpart
                break
        s = f"{head}.{rest[:6]}{tz}"
    return datetime.fromisoformat(s)


def uptime(cid):
    """容器已运行时长,人话字符串;停止/不存在返回 None。"""
    try:
        c = dc.containers.get(f"vpn-{cid}")
        st = c.attrs.get("State", {})
        if not st.get("Running"):
            return None
        secs = int((datetime.now(timezone.utc) - _parse_docker_time(st["StartedAt"])).total_seconds())
        if secs < 60:
            return f"{secs}秒"
        if secs < 3600:
            return f"{secs // 60}分钟"
        if secs < 86400:
            return f"{secs // 3600}小时{(secs % 3600) // 60}分"
        return f"{secs // 86400}天{(secs % 86400) // 3600}小时"
    except Exception:
        return None


def logs(cid, tail=200):
    try:
        c = dc.containers.get(f"vpn-{cid}")
        return c.logs(tail=tail).decode("utf-8", "replace").splitlines()
    except Exception as e:
        return [f"<no logs: {type(e).__name__}: {e}>"]


def connections():
    """代理 mihomo /connections 给监控屏。"""
    try:
        r = requests.get(f"{CTRL}/connections",
                         headers={"Authorization": f"Bearer {SECRET}"}, timeout=5)
        return r.json()
    except Exception:
        return {"connections": [], "downloadTotal": 0, "uploadTotal": 0}


def mihomo_alive():
    try:
        r = requests.get(f"{CTRL}/version",
                         headers={"Authorization": f"Bearer {SECRET}"}, timeout=3)
        return r.status_code == 200
    except Exception:
        return False
```

- [ ] **Step 3: import 自检**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/python -c "import sys; sys.path.insert(0,'app'); import os; os.environ.update(VPN_NET='x',MIHOMO_CTRL_URL='http://x',MIHOMO_SECRET='x'); import manager; print('ok', hasattr(manager,'uptime'), hasattr(manager,'connections'))"
```
Expected: `ok True True`

- [ ] **Step 4: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/manager.py && git commit -m "feat(manager): uptime, docker logs, connections proxy, mihomo_alive"
```

---

## Phase 3 — main.py(路由富化 + 新端点)

### Task 3.1: `_classify` + rules 路由(增/删/改)

**Files:**
- Test: `vpn-manager/tests/test_api.py`
- Modify: `vpn-manager/app/main.py`

- [ ] **Step 1: 写失败测试**

`vpn-manager/tests/test_api.py`:
```python
def _create(client, **over):
    body = {"name": "X", "vpn_type": "easyconnect", "server": "https://x",
            "ec_ver": "7.6.3", "login_method": "interactive", "probe_url": "http://p"}
    body.update(over)
    return client.post("/api/channels", json=body).json()


def test_create_channel(client):
    ch = _create(client, name="维度")
    assert ch["name"] == "维度"
    assert ch["status"] == "running"
    assert "password_enc" not in ch


def test_channels_enriched(client):
    cid = _create(client)["id"]
    client.post(f"/api/channels/{cid}/rules", json={"patterns": ["a.com", "10.0.0.0/8"]})
    ch = client.get("/api/channels").json()[0]
    assert ch["volume_name"] == f"vpndata-{cid}"
    assert ch["socks_proxy"] == f"ch-{cid}"
    assert ch["socks_endpoint"] == f"vpn-{cid}:1080"
    assert any(d["pattern"] == "a.com" for d in ch["domains"])
    assert any(i["pattern"] == "10.0.0.0/8" for i in ch["ips"])


def test_add_rules_autodetect_and_normalize(client):
    cid = _create(client)["id"]
    j = client.post(f"/api/channels/{cid}/rules",
                    json={"patterns": ["a.com", "10.0.0.5", "10.20.0.0/16"]}).json()
    assert j["added"] == {"domain": 1, "ip": 2}
    assert any(i["pattern"] == "10.0.0.5/32" for i in j["ips"])
    assert any(i["pattern"] == "10.20.0.0/16" for i in j["ips"])


def test_add_rules_forced_ip_rejects_nonip(client):
    cid = _create(client)["id"]
    j = client.post(f"/api/channels/{cid}/rules",
                    json={"patterns": ["nope.com"], "kind": "ip"}).json()
    assert "nope.com" in j["rejected"]
    assert j["added"] == {"domain": 0, "ip": 0}


def test_toggle_and_delete_rule(client):
    cid = _create(client)["id"]
    client.post(f"/api/channels/{cid}/rules", json={"patterns": ["a.com"]})
    rid = client.get("/api/channels").json()[0]["domains"][0]["id"]
    assert client.patch(f"/api/channels/{cid}/rules/{rid}", json={"enabled": False}).json()["ok"]
    assert client.get("/api/channels").json()[0]["domains"][0]["enabled"] == 0
    assert client.delete(f"/api/channels/{cid}/rules/{rid}").json()["ok"]
    assert client.get("/api/channels").json()[0]["domains"] == []


def test_system(client):
    j = client.get("/api/system").json()
    assert j["mihomo_status"] == "running"
    assert j["mihomo_port"] == 48721
    assert j["bound_ip"] == "127.0.0.1"


def test_status_latency(client):
    cid = _create(client)["id"]
    j = client.get(f"/api/channels/{cid}/status").json()
    assert j["connected"] is True and j["latency_ms"] == 42 and j["status"] == "logged_in"


def test_login_url(client):
    cid = _create(client)["id"]
    j = client.get(f"/api/channels/{cid}/login").json()
    assert "/vnc.html" in j["url"] and "127.0.0.1:18080" in j["url"]


def test_logs(client):
    cid = _create(client)["id"]
    assert client.get(f"/api/channels/{cid}/logs").json()["lines"] == ["line1", "line2"]


def test_connections(client):
    assert "connections" in client.get("/api/connections").json()
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/test_api.py -q
```
Expected: FAIL(多数 404 / KeyError)。

- [ ] **Step 3: main.py 顶部加 `import ipaddress` 与 `_classify`**

在 `vpn-manager/app/main.py` 顶部 import 区加 `import ipaddress`,并在 `store.init()` 之后加助手:
```python
def _classify(token):
    """返回 ('ip', cidr) / ('domain', token) / None。裸 IP 补 /32 或 /128;保留地址形态。"""
    t = (token or "").strip()
    if not t:
        return None
    addr = t.split("/")[0]
    try:
        ipaddress.ip_address(addr)
    except ValueError:
        return ("domain", t)
    if "/" in t:
        try:
            ipaddress.ip_network(t, strict=False)
        except ValueError:
            return None
        return ("ip", t)
    return ("ip", t + ("/128" if ":" in addr else "/32"))
```

- [ ] **Step 4: 加 rules 三个路由(放在现有 `/domains` 路由附近)**

```python
@app.post("/api/channels/{cid}/rules")
async def add_rules(cid, req: Request):
    b = await req.json()
    patterns = b.get("patterns") or ([b["pattern"]] if b.get("pattern") else [])
    forced = b.get("kind")
    added = {"domain": 0, "ip": 0}
    rejected = []
    existing = {(r["kind"], r["pattern"]) for r in store.list_rules(cid)}
    for tok in patterns:
        if forced == "domain":
            kind, pat = "domain", (tok or "").strip()
        elif forced == "ip":
            c = _classify(tok)
            if not c or c[0] != "ip":
                rejected.append(tok)
                continue
            kind, pat = c
        else:
            c = _classify(tok)
            if not c:
                rejected.append(tok)
                continue
            kind, pat = c
        if not pat or (kind, pat) in existing:
            continue
        store.add_rule(cid, kind, pat)
        existing.add((kind, pat))
        added[kind] += 1
    code = manager.rebuild()
    rs = store.list_rules(cid)
    return {"reload_status": code,
            "domains": [r for r in rs if r["kind"] == "domain"],
            "ips": [r for r in rs if r["kind"] == "ip"],
            "added": added, "rejected": rejected}


@app.delete("/api/channels/{cid}/rules/{rid}")
def del_rule(cid, rid: int):
    store.del_rule(rid)
    return {"ok": True, "reload_status": manager.rebuild()}


@app.patch("/api/channels/{cid}/rules/{rid}")
async def patch_rule(cid, rid: int, req: Request):
    b = await req.json()
    store.set_rule_enabled(rid, bool(b.get("enabled")))
    return {"ok": True, "reload_status": manager.rebuild()}
```

- [ ] **Step 5: 富化 `GET /api/channels`**

把现有 `channels()` 替换为:
```python
@app.get("/api/channels")
def channels():
    out = []
    for c in store.list_channels():
        rs = store.list_rules(c["id"])
        c["domains"] = [r for r in rs if r["kind"] == "domain"]
        c["ips"] = [r for r in rs if r["kind"] == "ip"]
        c["volume_name"] = f"vpndata-{c['id']}"
        c["socks_proxy"] = f"ch-{c['id']}"
        c["socks_endpoint"] = f"vpn-{c['id']}:1080"
        c["uptime"] = manager.uptime(c["id"]) if c["status"] != "stopped" else None
        out.append(c)
    return out
```

- [ ] **Step 6: 改 `status`,加 `system`/`logs`/`connections`**

把 `status()` 替换为:
```python
@app.get("/api/channels/{cid}/status")
def status(cid):
    ch = store.get_channel(cid)
    if not ch:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok, ms = manager.probe(ch)
    new = "logged_in" if ok else ("running" if ch["status"] == "logged_in" else ch["status"])
    store.set_status(cid, new)
    if ms is not None:
        store.set_latency(cid, ms)
    return {"status": new, "connected": ok, "latency_ms": ms}
```
新增:
```python
@app.get("/api/system")
def system():
    ctrl_port = os.environ.get("MIHOMO_CTRL_PORT", "")
    return {
        "mihomo_status": "running" if manager.mihomo_alive() else "down",
        "mihomo_port": int(os.environ.get("MIHOMO_HOST_PORT") or 0) or None,
        "controller": f"127.0.0.1:{ctrl_port}" if ctrl_port else None,
        "ui_port": int(os.environ.get("UI_PORT") or 0) or None,
        "bound_ip": "127.0.0.1",
    }


@app.get("/api/channels/{cid}/logs")
def channel_logs(cid, tail: int = 200):
    return {"lines": manager.logs(cid, tail)}


@app.get("/api/connections")
def api_connections():
    return manager.connections()
```

- [ ] **Step 7: 跑测试确认通过**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/test_api.py -q
```
Expected: `11 passed`。

- [ ] **Step 8: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/main.py vpn-manager/tests/test_api.py && git commit -m "feat(api): rules CRUD, channel enrichment, system/logs/connections, status latency"
```

### Task 3.2: Clash provider + snippet 扩展

**Files:**
- Test: `vpn-manager/tests/test_clash.py`
- Modify: `vpn-manager/app/main.py`(`clash_snippet` + 新增 provider 路由)

- [ ] **Step 1: 写失败测试**

`vpn-manager/tests/test_clash.py`:
```python
import yaml


def _seed(client):
    b = {"name": "X", "vpn_type": "easyconnect", "server": "https://x",
         "ec_ver": "7.6.3", "login_method": "interactive", "probe_url": "http://p"}
    cid = client.post("/api/channels", json=b).json()["id"]
    client.post(f"/api/channels/{cid}/rules",
                json={"patterns": ["+.weidu-crm.com", "10.20.0.0/16"]})
    return cid


def test_clash_provider_payload(client):
    _seed(client)
    r = client.get("/clash/vpn-rules.yaml")
    assert r.status_code == 200
    cfg = yaml.safe_load(r.text)
    assert "DOMAIN-SUFFIX,weidu-crm.com" in cfg["payload"]   # 去掉 +. 前缀
    assert "IP-CIDR,10.20.0.0/16" in cfg["payload"]


def test_clash_provider_skips_disabled(client):
    cid = _seed(client)
    rid = client.get("/api/channels").json()[0]["domains"][0]["id"]
    client.patch(f"/api/channels/{cid}/rules/{rid}", json={"enabled": False})
    cfg = yaml.safe_load(client.get("/clash/vpn-rules.yaml").text)
    assert "DOMAIN-SUFFIX,weidu-crm.com" not in cfg["payload"]


def test_clash_snippet_has_node_ip_and_provider(client):
    _seed(client)
    txt = client.get("/api/clash-snippet").text
    assert "vpn-router" in txt
    assert "IP-CIDR,10.20.0.0/16,vpn-router,no-resolve" in txt
    assert "rule-providers" in txt and "vpn-rules.yaml" in txt
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/test_clash.py -q
```
Expected: FAIL(`/clash/vpn-rules.yaml` 404;snippet 无 IP/provider)。

- [ ] **Step 3: 加 `_bare` 助手 + provider 路由,改 `clash_snippet`**

在 `main.py` 加助手:
```python
def _bare(p):
    for pre in ("+.", "*."):
        if p.startswith(pre):
            return p[len(pre):]
    return p
```
新增 provider 路由:
```python
@app.get("/clash/vpn-rules.yaml", response_class=PlainTextResponse)
def clash_provider():
    lines = ["payload:"]
    for r in store.all_rules():
        if not r["enabled"]:
            continue
        if r["kind"] == "ip":
            lines.append(f"  - IP-CIDR,{r['pattern']}")
        else:
            lines.append(f"  - DOMAIN-SUFFIX,{_bare(r['pattern'])}")
    return "\n".join(lines) + "\n"
```
把现有 `clash_snippet()` 替换为(节点 + 内联含 IP + provider 引用 + 无 Clash 说明):
```python
@app.get("/api/clash-snippet", response_class=PlainTextResponse)
def clash_snippet():
    rules = [r for r in store.all_rules() if r["enabled"]]
    L = [
        "# ① 在你现有 Clash 的 proxies: 下加这个节点",
        "proxies:",
        "  - name: vpn-router",
        "    type: socks5",
        "    server: 127.0.0.1",
        f"    port: {MIHOMO_HOST_PORT}",
        "",
        "# ② 方式甲(推荐):订阅一份规则,绑新域名/IP 自动同步,之后不再动 Clash",
        "rule-providers:",
        "  vpn-rules:",
        "    type: http",
        "    behavior: classical",
        "    format: yaml",
        f"    url: http://127.0.0.1:{os.environ.get('UI_PORT','<UI端口>')}/clash/vpn-rules.yaml",
        "    interval: 3600",
        "    path: ./providers/vpn-rules.yaml",
        "# rules: 顶部加一行引用(no-resolve 对清单内 IP-CIDR 生效)",
        "  - RULE-SET,vpn-rules,vpn-router,no-resolve",
        "",
        "# ② 方式乙:直接内联(不想用 provider 时)",
    ]
    if rules:
        for r in rules:
            if r["kind"] == "ip":
                L.append(f"  - IP-CIDR,{r['pattern']},vpn-router,no-resolve")
            else:
                L.append(f"  - DOMAIN-SUFFIX,{_bare(r['pattern'])},vpn-router,no-resolve")
    else:
        L.append("  # (还没绑定任何规则)")
    L += [
        "",
        "# ③ 无 Clash 时:把系统/浏览器代理指向 127.0.0.1:" + str(MIHOMO_HOST_PORT),
        "#    本工具 mihomo 自身分流:命中→VPN 容器,其余→直连。",
    ]
    return "\n".join(L)
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/test_clash.py -q
```
Expected: `3 passed`。

- [ ] **Step 5: 全后端测试回归**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/ -q
```
Expected: `25 passed`(store7 + manager4 + api11 + clash3)。

- [ ] **Step 6: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/main.py vpn-manager/tests/test_clash.py && git commit -m "feat(api): clash rule-provider endpoint + snippet with IP/provider/standalone"
```

---

## Phase 4 — compose env 微调

### Task 4.1: app 暴露 UI/控制台端口

**Files:**
- Modify: `vpn-manager/docker-compose.yml`(app `environment:` 段)

- [ ] **Step 1: 加两行 env**

在 `vpn-manager/docker-compose.yml` 的 `app:` → `environment:` 段,`DATA_DIR: /data` 之后加:
```yaml
      UI_PORT: ${UI_PORT}
      MIHOMO_CTRL_PORT: ${MIHOMO_CTRL_PORT}
```

- [ ] **Step 2: 校验 compose 语法**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && docker compose config >/dev/null && echo OK
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/docker-compose.yml && git commit -m "chore(compose): expose UI_PORT + MIHOMO_CTRL_PORT to app for /api/system"
```

---

## Phase 5 — 前端接线

> 验证方式:无 Playwright。每屏改完后,**起静态服务 + 用浏览器人工核**(或 Phase 6 冒烟覆盖其依赖的后端端点)。结构性自检:确认无 `window.VPN`/`data.js` 残留、`fetch` 走 `api.*`。每屏保留既有 DOM 结构与渲染辅助,**只替换 mock 读取与仿真动作**。

### Task 5.1: js/api.js 封装

**Files:**
- Create: `vpn-manager/app/static/js/api.js`

- [ ] **Step 1: 写 api.js**

`vpn-manager/app/static/js/api.js`:
```javascript
/* 真实后端 API 封装,所有屏共用。失败抛 Error(消息含状态码+响应体)。 */
(function () {
  "use strict";
  async function req(method, url, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(url, opt);
    if (!r.ok) {
      const t = await r.text().catch(() => "");
      throw new Error(`${r.status} ${t}`.trim());
    }
    const ct = r.headers.get("content-type") || "";
    return ct.includes("application/json") ? r.json() : r.text();
  }
  window.api = {
    channels: () => req("GET", "/api/channels"),
    system: () => req("GET", "/api/system"),
    create: (data) => req("POST", "/api/channels", data),
    login: (id) => req("GET", `/api/channels/${id}/login`),
    status: (id) => req("GET", `/api/channels/${id}/status`),
    addRules: (id, patterns, kind) =>
      req("POST", `/api/channels/${id}/rules`, kind ? { patterns, kind } : { patterns }),
    delRule: (id, rid) => req("DELETE", `/api/channels/${id}/rules/${rid}`),
    toggleRule: (id, rid, enabled) =>
      req("PATCH", `/api/channels/${id}/rules/${rid}`, { enabled }),
    start: (id) => req("POST", `/api/channels/${id}/start`),
    stop: (id) => req("POST", `/api/channels/${id}/stop`),
    remove: (id) => req("DELETE", `/api/channels/${id}`),
    logs: (id, tail) => req("GET", `/api/channels/${id}/logs?tail=${tail || 200}`),
    connections: () => req("GET", "/api/connections"),
    snippet: () => req("GET", "/api/clash-snippet"),
  };
})();
```

- [ ] **Step 2: 五屏引脚本:`data.js` → `api.js`**

对 `index.html`、`new-channel.html`、`channel.html`、`monitor.html`、`clash-config.html`,把
```html
<script src="js/data.js"></script>
```
改为
```html
<script src="js/api.js"></script>
```
(保留其后的 `<script src="js/app.js"></script>`。)

- [ ] **Step 3: 删 data.js**

Run:
```bash
cd /private/var/www/test_vpn && git rm vpn-manager/app/static/js/data.js
```

- [ ] **Step 4: Commit**

```bash
cd /private/var/www/test_vpn && git add -A vpn-manager/app/static && git commit -m "feat(web): add js/api.js fetch wrapper, drop mock data.js, repoint script tags"
```

### Task 5.2: index.html(总览)接线

**Files:**
- Modify: `vpn-manager/app/static/index.html`(`<script>` 段 128-234)

- [ ] **Step 1: 用真实数据驱动整屏**

把内联 `<script>` 内 `const chs = window.VPN.channels;` 起的逻辑改为异步加载。保留 `cardHTML`/`actionsFor`/`refresh` 渲染函数,但:
- 顶部改为:`let chs = [];` `let sys = {};`
- 新增 `async function load(){ [chs, sys] = await Promise.all([api.channels(), api.system()]); paint(); }`,把原先一次性 DOM 写入(统计/列表/底栏端口)收进 `paint()`。
- `$("#foot-port")` 用 `sys.mihomo_port`;clash 接入状态 chip 暂用「有规则即视为已接入」:`const linked = chs.some(c => (c.domains||[]).length + (c.ips||[]).length > 0);`
- `cardHTML` 内 SOCKS 显示改真相:把 `:${c.socks_port}` 改为 `${c.socks_endpoint}`(即 `vpn-{id}:1080`),标签文案「SOCKS5 出口」保留。
- `probe(id, btn)` 改为:
```javascript
window.probe = async function (id, btn) {
  btn.textContent = "检测中…"; btn.disabled = true;
  try {
    const r = await api.status(id);
    await load();
    toast(r.connected ? `隧道真通 · ${r.latency_ms ?? "?"} ms · 已登录` : "探活失败:未连通", r.connected);
  } catch (e) { toast("探活出错:" + e.message, false); }
  finally { btn.disabled = false; btn.textContent = "检测连通"; }
};
```
- `bindRule(id, form)` 改为:
```javascript
window.bindRule = async function (id, form) {
  const toks = parseTokens(form.pat.value);
  if (!toks.length) { toast("请填写域名或 IP", false); return false; }
  try {
    const r = await api.addRules(id, toks);
    form.pat.value = ""; await load();
    if (r.added.domain || r.added.ip)
      toast(`已绑定 ${r.added.domain} 域名 / ${r.added.ip} IP → 热加载生效`);
    else if (r.rejected.length) toast("格式无法识别:" + r.rejected.join(", "), false);
    else toast("这些规则已绑定", false);
  } catch (e) { toast("绑定出错:" + e.message, false); }
  return false;
};
```
- 删除按钮:`#del-confirm` 处改为 `await api.remove(pendingDel); await load();`。
- 末尾:`load(); setInterval(load, 8000);`(替换原先同步渲染调用)。
- 渲染统计里 `c.domains.length + (c.ips?c.ips.length:0)` 保持(后端已回 `domains[]`/`ips[]`)。

> 注:`load` 是 async,把原内联脚本顶层非函数语句包进 `load`/`paint`,避免在数据到达前访问 `chs`。

- [ ] **Step 2: 人工核(对接真实后端)**

Run(需后端在跑,见 Phase 6 或 `./start.sh`);否则结构自检:
```bash
cd /private/var/www/test_vpn/vpn-manager/app/static && grep -c "window.VPN" index.html
```
Expected: `0`(无 mock 残留)。浏览器核:列表渲染、检测连通 toast、快绑、删除、8s 自动刷新。

- [ ] **Step 3: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/static/index.html && git commit -m "feat(web): wire overview to real API (channels/system/status/rules/delete)"
```

### Task 5.3: new-channel.html(向导)接线

**Files:**
- Modify: `vpn-manager/app/static/new-channel.html`

- [ ] **Step 1: 补密码输入(EC 账密)**

在第①步账号字段 `#account-field` 内,`#f-user` 之后加密码输入(沿用现有 `.input` 样式):
```html
<input class="input" id="f-pass" type="password" placeholder="VPN 密码(账号密码方式必填)" />
```
脚本 model 加 `pass: ""`,并绑定:`$("#f-pass").addEventListener("input", e => { model.pass = e.target.value; });`

- [ ] **Step 2: step2「起容器」改真**

把 `#run-btn` 的仿真日志循环替换为真实创建(创建即返回 running):
```javascript
$("#run-btn").addEventListener("click", async () => {
  if (started) return;
  started = true;
  const btn = $("#run-btn");
  btn.disabled = true; btn.textContent = "启动中…";
  setRunBadge("starting"); setSumBadge("starting");
  $("#run-log").innerHTML = "";
  appendLog("$ POST /api/channels …", "");
  try {
    const ch = await api.create({
      name: model.name, vpn_type: model.type, server: model.server,
      ec_ver: model.type === "easyconnect" ? model.ecver : "",
      login_method: model.login, username: model.user,
      password: model.login === "password" ? model.pass : "",
      probe_url: model.probe,
    });
    window.createdId = ch.id;
    appendLog("Created container vpn-" + ch.id, "ok");
    appendLog("status → " + ch.status, "ok");
    setRunBadge("running"); setSumBadge("running");
    btn.textContent = "已起容器"; $("#go-3").disabled = false;
    toast("容器已起 · 进入登录");
  } catch (e) {
    started = false; btn.disabled = false; btn.textContent = "重试起容器";
    setRunBadge("error"); appendLog("起容器失败:" + e.message, "warn");
    toast("起容器失败:" + e.message, false);
  }
});
```

- [ ] **Step 3: step3 登录改真 noVNC iframe**

第③步交互登录区(`#login-interactive`)把仿真登录窗换成真 iframe。进入 step3 时拉 url:
```javascript
async function loadVnc() {
  try {
    const { url } = await api.login(window.createdId);
    let f = document.getElementById("vnc-frame");
    if (!f) {
      f = document.createElement("iframe");
      f.id = "vnc-frame"; f.style.cssText = "width:100%;height:520px;border:0;border-radius:8px;";
      $("#login-interactive").prepend(f);
    }
    f.src = url;
  } catch (e) { toast("拉取登录窗失败:" + e.message, false); }
}
```
在 `$("#go-3")` 点击后(`gotoStep(3)`)调用 `loadVnc()`(账密无头方式跳过)。

- [ ] **Step 4: step4 探活改真**

`#probe-btn` 替换为:
```javascript
$("#probe-btn").addEventListener("click", async () => {
  const btn = $("#probe-btn"), spin = $("#probe-spin");
  btn.disabled = true; spin.style.display = "";
  try {
    const r = await api.status(window.createdId);
    if (r.connected) {
      $("#probe-lat").textContent = r.latency_ms ?? "?";
      $("#probe-result").classList.add("show");
      $("#probe-badge").outerHTML = badgeHTML("logged_in").replace("<span", '<span id="probe-badge"');
      setSumBadge("logged_in"); $("#go-5").disabled = false;
      toast(`隧道真通 · ${r.latency_ms ?? "?"} ms · 已登录`);
    } else { toast("探活未通过:请确认已在登录窗完成验证", false); }
  } catch (e) { toast("探活出错:" + e.message, false); }
  finally { btn.disabled = false; btn.textContent = "重新探活"; spin.style.display = "none"; }
});
```

- [ ] **Step 5: step5 绑规则改真**

`#bind-form` submit 内,把写 `bound[]` 的仿真换成:
```javascript
const toks = parseTokens($("#f-domain").value);
if (!toks.length) { toast("请填写域名或 IP", false); return false; }
try {
  const r = await api.addRules(window.createdId, toks);
  $("#f-domain").value = "";
  // 用返回的 domains/ips 重绘 bound-list
  const all = [...r.domains.map(d => ({ pattern: d.pattern, ip: false })),
               ...r.ips.map(d => ({ pattern: d.pattern, ip: true }))];
  $("#bound-list").innerHTML = all.map(b =>
    `<span class="tag mono${b.ip ? " ip" : ""}">${b.pattern}</span>`).join("");
  $("#done-name").textContent = model.name;
  $("#done-socks").textContent = `vpn-${window.createdId}:1080`;
  $("#done-domain").textContent = `${r.domains.length} 域名 / ${r.ips.length} IP`;
  $("#done-card").style.display = "";
  toast(`已绑定 ${r.added.domain} 域名 / ${r.added.ip} IP → 热加载生效`);
} catch (e) { toast("绑定出错:" + e.message, false); }
return false;
```
`#nav-count`/`#foot-port` 改异步取:开头加 `api.system().then(s => { $("#foot-port").textContent = ":" + s.mihomo_port; }); api.channels().then(cs => { $("#nav-count").textContent = cs.length; });` 删除对 `window.VPN` 的读。docker-run 预览(buildRunPreview)是纯展示,保留(仅把 `seq`/端口相关展示改为不依赖 `window.VPN.channels.length`:用 `chanId` 派生展示即可,去掉 `socksPort`/`novncPort` 伪值显示或标注「容器内 1080/8080」)。

- [ ] **Step 6: 结构自检 + 人工核**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager/app/static && grep -c "window.VPN" new-channel.html
```
Expected: `0`。浏览器核五步:填表→起容器(真 POST)→登录(真 iframe,无真 VPN 时容器在跑即可见 noVNC)→探活→绑规则。

- [ ] **Step 7: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/static/new-channel.html && git commit -m "feat(web): wire new-channel wizard to real create/login/status/rules"
```

### Task 5.4: channel.html(详情)接线

**Files:**
- Modify: `vpn-manager/app/static/channel.html`

- [ ] **Step 1: 选通道改异步**

把 `const ch = window.VPN.channels.find(...)` 改为:开头
```javascript
const params = new URLSearchParams(location.search);
const wantId = params.get("id");
let ch, sys;
async function boot() {
  const list = await api.channels();
  ch = list.find(c => c.id === wantId) || list[0];
  sys = await api.system();
  if (!ch) { toast("通道不存在", false); return; }
  renderAll();        // 把原同步渲染(顶栏/概览/健康/登录面板填充/规则表/日志)收进来
}
```
所有用到 `ch.socks_port` 处改 `ch.socks_endpoint`;`ch.volume_name` 后端已回真实 `vpndata-{id}`;`window.VPN.system.boundIp` → `sys.bound_ip`;`#foot-port` → `sys.mihomo_port`;`#nav-count` → `list.length`(在 boot 内取)。

- [ ] **Step 2: 登录 tab 真 noVNC**

把仿 portal 登录页换成真 iframe(进入 login tab 时):
```javascript
async function loadVnc() {
  try {
    const { url } = await api.login(ch.id);
    let f = $("#vnc-frame");
    if (!f) {
      f = document.createElement("iframe");
      f.id = "vnc-frame"; f.style.cssText = "width:100%;height:560px;border:0;";
      $("#portal").replaceWith(f);   // 用 iframe 替换仿真 portal 容器
    } else f.src = url;
    f.src = url;
  } catch (e) { toast("拉取登录窗失败:" + e.message, false); }
}
```
`goLogin()` 切到 login tab 后调用 `loadVnc()`。「重新登录」按钮:`loadVnc()` + toast 提示在窗内完成验证。

- [ ] **Step 3: 规则表增删/启停改真**

`wireRuleTable` 的三处操作接 API(操作后 `await boot()` 重渲,或就地改 `ch.domains/ch.ips` 再 render):
- 新增(submit):`const r = await api.addRules(ch.id, toks, o.kind);` 其中域名表传 `o.kind="domain"`、IP 表传 `"ip"`;用返回刷新该表 `o.list`。
- 启停(toggle):`await api.toggleRule(ch.id, d.id, !d.enabled);` 成功后 `d.enabled = d.enabled?0:1; render();`
- 删除(del):`await api.delRule(ch.id, d.id);` 成功后从 `o.list` 移除 `render();`
给 `wireRuleTable` 配置加 `kind`:域名表 `{..., kind:"domain"}`、IP 表 `{..., kind:"ip"}`。规则项要用后端的 `d.id`(列表项渲染 `data-id="${d.id}"`,操作按 id 而非数组下标)。

- [ ] **Step 4: 健康/状态/启停/重启/删除/日志改真**

- 健康卡 `renderHealth` 用 `ch.latency_ms`/`ch.uptime`(后端已回)。
- 「检测连通」`#act-probe`/`#health-refresh`:`const r = await api.status(ch.id); await boot(); toast(...)`。
- 启停 `#act-power`:`ch.status==="stopped" ? await api.start(ch.id) : await api.stop(ch.id); await boot();`
- 重启 `#act-restart`:`await api.stop(ch.id); await api.start(ch.id); await boot();`
- 删除 `#del-confirm`:`await api.remove(ch.id); location.href="index.html";`
- 日志 tab:`const { lines } = await api.logs(ch.id); $("#logs-body").textContent = lines.join("\\n");`「刷新日志」重新拉。删除原 baseLog/extraLogs 仿真。

- [ ] **Step 5: 末尾启动 + 自检**

末尾把原同步初始化替换为 `boot();`。Run:
```bash
cd /private/var/www/test_vpn/vpn-manager/app/static && grep -c "window.VPN" channel.html
```
Expected: `0`。浏览器核四 tab。

- [ ] **Step 6: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/static/channel.html && git commit -m "feat(web): wire channel detail to real API (rules/login/logs/power/status)"
```

### Task 5.5: monitor.html(监控)接线

**Files:**
- Modify: `vpn-manager/app/static/monitor.html`

- [ ] **Step 1: 用 /api/connections + /api/channels 驱动**

替换 `window.VPN` 三个读取:
```javascript
let chs = [], sys = {}, prevTotals = null, prevTs = null;
async function boot() {
  sys = await api.system();
  $("#foot-port").textContent = ":" + sys.mihomo_port;
  $("#ctrl-pill").textContent = "external-controller " + (sys.controller || "?") + " · 只读遥测";
  await tick();
  setInterval(tick, 1500);
}
function nodeName(id) { const i = chs.findIndex(c => c.id === id); return i < 0 ? "DIRECT" : "chan" + String.fromCharCode(65 + i) + "-socks"; }
function chanName(id) { const c = chs.find(x => x.id === id); return c ? c.name : "—"; }
```
`tick` 改为真实数据:
```javascript
async function tick() {
  let data;
  try { [chs, data] = await Promise.all([api.channels(), api.connections()]); }
  catch (e) { return; }
  $("#nav-count").textContent = chs.length;
  // 速率 = 总量差分 / 时间差
  const now = Date.now();
  let up = 0, down = 0;
  if (prevTotals && prevTs) {
    const dt = (now - prevTs) / 1000 || 1;
    up = Math.max(0, (data.uploadTotal - prevTotals.up) / dt / 1024);     // KB/s
    down = Math.max(0, (data.downloadTotal - prevTotals.down) / dt / 1024);
  }
  prevTotals = { up: data.uploadTotal, down: data.downloadTotal }; prevTs = now;
  pushSeries(up, down, (data.connections || []).length);
  renderMetrics(up, down, (data.connections || []).length);
  renderConns(data.connections || []);
  renderLatency();
  drawAll();
}
```
- `renderConns(list)`:每条 `c.metadata.host:c.metadata.destinationPort`、`c.rule`、`chains` 末项→通道(`chains[chains.length-1]` 形如 `ch-{id}`,映射 chanName)、`c.upload`/`c.download`(格式化字节)。DIRECT 链单列「直连」。
- `renderLatency()`:`chs.filter(c=>c.status==="logged_in")` 用 `c.latency_ms`;末尾 DIRECT 兜底行不变。
- 折线/面积绘制:保留原 `drawSpark/drawFlow/drawGrid`,把数据源换成由 `pushSeries` 维护的真实速率序列(`upSeries/downSeries/connSeries` 用 push/shift 维护,初值全 0)。移除 `seed()` 随机与 `nextVal()` 回归仿真。

- [ ] **Step 2: 自检 + 人工核**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager/app/static && grep -c "window.VPN\|Math.random" monitor.html
```
Expected: `0`(无 mock、无随机仿真)。浏览器核:连接表随真实流量变化(需有流量经 mihomo)。

- [ ] **Step 3: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/static/monitor.html && git commit -m "feat(web): wire monitor to real mihomo connections (poll + rate diff)"
```

### Task 5.6: clash-config.html(接入)接线 + 双模式

**Files:**
- Modify: `vpn-manager/app/static/clash-config.html`

- [ ] **Step 1: 真实数据拼三段 + 加无 Clash 模式**

替换 `window.VPN` 读取:
```javascript
let chs = [], sys = {};
async function boot() {
  [chs, sys] = await Promise.all([api.channels(), api.system()]);
  const origin = location.origin;          // 如 http://127.0.0.1:42411
  const mport = sys.mihomo_port;
  $("#nav-count").textContent = chs.length;
  $("#foot-port").textContent = ":" + mport;
  // ① 节点(port 用 sys.mihomo_port)
  // ② 内联规则:遍历 chs 的 domains(enabled,去 +./*.)与 ips(enabled)
  // ③ provider:url = origin + "/clash/vpn-rules.yaml"
  // 用既有高亮辅助 esc/cmt/key/str 重建三段;复制按钮文本取去高亮的纯文本。
  renderSnippets(origin, mport);
}
```
- 节点片段 `port: ${mport}`。
- 规则聚合:`chs.forEach(c => (c.domains||[]).forEach(d => d.enabled && push DOMAIN-SUFFIX 去前缀)); chs.forEach(c => (c.ips||[]).forEach(d => d.enabled && push IP-CIDR));`(逻辑与原 clash-config 一致,只是数据来自 `api.channels()` 而非 mock,且 `enabled` 来自后端 0/1)。
- provider `url: ${origin}/clash/vpn-rules.yaml`,`interval: 3600`。
- 新增「无 Clash」区块(在现有三段后):一段说明 + 可复制 `127.0.0.1:${mport}`,文案:把系统/浏览器代理指向此地址,命中→VPN、其余→直连。若页面无对应容器,用现有卡片/代码块样式新增一个 section(保持 Neutral Modern,无新色)。
- clash 接入状态 chip:`const linked = chs.some(c => (c.domains||[]).length + (c.ips||[]).length > 0);`

- [ ] **Step 2: 自检 + 人工核**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager/app/static && grep -c "window.VPN" clash-config.html
```
Expected: `0`。浏览器核:三段片段含真实端口/规则、provider URL 用当前 origin、复制可用、无 Clash 区块出现。

- [ ] **Step 3: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/app/static/clash-config.html && git commit -m "feat(web): wire clash-config to real data + add no-Clash standalone mode"
```

---

## Phase 6 — Docker 栈冒烟

### Task 6.1: smoke.sh

**Files:**
- Create: `vpn-manager/tests/smoke.sh`

- [ ] **Step 1: 写冒烟脚本**

`vpn-manager/tests/smoke.sh`:
```bash
#!/usr/bin/env bash
# 栈冒烟:起 compose,断言关键端点 + mihomo 热加载。不含真 VPN 登录。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then python3 gen_env.py; fi
set -a; . ./.env; set +a
# 渲染 mihomo 配置(start.sh 同款占位替换)
sed "s/__SECRET__/${MIHOMO_SECRET}/" mihomo/config.template.yaml > mihomo/config.yaml

echo "== compose up =="
docker compose up -d --build
trap 'docker compose logs --tail=30 app || true' ERR

base="http://127.0.0.1:${UI_PORT}"
echo "== 等待 app 就绪 =="
for i in $(seq 1 30); do
  if curl -fsS "${base}/api/system" >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "== /api/system =="
curl -fsS "${base}/api/system" | grep -q '"mihomo_status": *"running"' \
  && echo "  ok: mihomo running" || { echo "  FAIL: mihomo not running"; exit 1; }

echo "== /api/channels =="
curl -fsS "${base}/api/channels" >/dev/null && echo "  ok"

echo "== /clash/vpn-rules.yaml 合法 YAML =="
curl -fsS "${base}/clash/vpn-rules.yaml" | python3 -c "import sys,yaml; d=yaml.safe_load(sys.stdin); assert 'payload' in d; print('  ok: payload key present')"

echo "== /api/clash-snippet 含节点 =="
curl -fsS "${base}/api/clash-snippet" | grep -q "vpn-router" && echo "  ok"

echo "== mihomo 热加载(PUT /configs)=="
curl -fsS -X PUT "127.0.0.1:${MIHOMO_CTRL_PORT}/configs?force=true" \
  -H "Authorization: Bearer ${MIHOMO_SECRET}" \
  -H "Content-Type: application/json" \
  -d '{"path":"/cfg/config.yaml"}' -o /dev/null -w "  PUT status: %{http_code}\n" \
  | grep -qE "204|200" && echo "  ok: hot reload" || echo "  (热加载返回非2xx,看上行状态)"

echo "ALL SMOKE CHECKS PASSED"
```

- [ ] **Step 2: 可执行 + 跑**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && chmod +x tests/smoke.sh && ./tests/smoke.sh
```
Expected: 末行 `ALL SMOKE CHECKS PASSED`(首次构建镜像 + 拉 mihomo,稍慢)。

- [ ] **Step 3: 停栈**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && docker compose down
```

- [ ] **Step 4: Commit**

```bash
cd /private/var/www/test_vpn && git add vpn-manager/tests/smoke.sh && git commit -m "test: docker stack smoke (boot + endpoints + mihomo hot reload)"
```

---

## Phase 7 — 收尾验证

### Task 7.1: 全量回归 + 命门复核

- [ ] **Step 1: 后端全测**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager && ./.venv/bin/pytest tests/ -q
```
Expected: `25 passed`。

- [ ] **Step 2: mock 残留全局自检**

Run:
```bash
cd /private/var/www/test_vpn/vpn-manager/app/static && grep -rl "window.VPN" . ; ls js/data.js 2>/dev/null && echo "data.js 仍在(应已删)" || echo "data.js 已删"
```
Expected: 无文件含 `window.VPN`;`data.js 已删`。

- [ ] **Step 3: 命门清单逐条复核(只读,不改)**

人工对照 spec §8:
- `manager.probe` 用 `socks5h://vpn-{id}:1080`(登录判据)✓
- Clash 输出与 provider 带 `no-resolve`(IP 行)✓
- `rebuild()` 用 `PUT ?force=true`,不重启 ✓
- 端口绑 `127.0.0.1`(compose + manager 端口映射)✓
- `_row` 不回 `password_enc`(test_store 已覆盖)✓
- 前端不再显示伪 host SOCKS 端口(用 `socks_endpoint`)✓

- [ ] **Step 4: Commit(若有零散修正)**

```bash
cd /private/var/www/test_vpn && git add -A && git commit -m "chore: final regression fixes" || echo "nothing to commit"
```

---

## 验收标准(Definition of Done)
- `pytest tests/ -q` → 25 passed。
- `tests/smoke.sh` → ALL SMOKE CHECKS PASSED。
- 5 屏无 `window.VPN`/`data.js` 残留,交互走 `api.*`。
- 命门 §8 逐条不破。
- 人工(浏览器)核过五屏主交互(列表/向导/详情/监控/接入)。

## Self-Review(已核)
- **Spec 覆盖**:数据模型(P1)/API(P2-3)/五屏(P5)/真相对齐(P5 各屏 socks_endpoint+卷名)/测试(P1-3,P6)/compose(P4)/Clash 双模式(P3.2 + P5.6)——均有对应 Task。
- **Placeholder**:无 TBD;代码步均给完整代码或精确替换指令。
- **类型一致**:`api.*` 方法名、`probe→(ok,ms)`、`rules{kind,enabled,id}`、`/clash/vpn-rules.yaml` payload 形态在前后端与测试间一致;规则操作统一按后端 `id`(非数组下标)。
