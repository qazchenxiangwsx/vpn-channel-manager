import os
import uuid
import random
import secrets
import ipaddress

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import store
import manager

HERE = os.path.dirname(__file__)
MIHOMO_HOST_PORT = os.environ.get("MIHOMO_HOST_PORT", "?")

app = FastAPI(title="VPN 通道管理器 (demo)")
store.init()


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


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as f:
        return f.read()


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


@app.post("/api/channels")
async def create(req: Request):
    b = await req.json()
    cid = uuid.uuid4().hex[:8]
    mac = "02:" + ":".join(f"{random.randint(0, 255):02x}" for _ in range(5))
    vnc = secrets.token_hex(4)  # 8 个十六进制字符 = tigervnc 8 字节上限内
    ch = {
        "id": cid, "name": b.get("name") or cid,
        "vpn_type": b.get("vpn_type", "easyconnect"),
        "server": b.get("server", ""), "ec_ver": b.get("ec_ver", "7.6.3"),
        "login_method": b.get("login_method", "interactive"),
        "username": b.get("username", ""), "password": b.get("password", ""),
        "vnc_password": vnc, "mac": mac, "probe_url": b.get("probe_url", ""),
        "status": "creating",
    }
    store.add_channel(ch)
    try:
        container_id, novnc = manager.create_channel(ch, vnc)
    except Exception as e:
        store.set_status(cid, "error")
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    store.set_container(cid, container_id, novnc, "running")
    manager.rebuild()
    return store.get_channel(cid)


@app.get("/api/channels/{cid}/login")
def login(cid):
    ch = store.get_channel(cid)
    if not ch or not ch.get("novnc_port"):
        return JSONResponse({"error": "no novnc port"}, status_code=404)
    manager.ensure_novnc_bridge(cid)   # arm64 镜像 websockify 自愈,否则 noVNC 连不上
    # path 必须带尾斜杠:镜像内 tinyproxy 把 /websockify 301 重定向到 /websockify/,
    # 而 WebSocket 握手不跟随 301 → 不加斜杠会「无法连接到服务器」。
    url = (f"http://127.0.0.1:{ch['novnc_port']}/vnc.html"
           f"?path=websockify/&autoconnect=true&resize=remote&password={ch['vnc_password']}")
    return {"url": url}


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


@app.post("/api/channels/{cid}/start")
def start(cid):
    manager.start(cid)
    store.set_status(cid, "running")
    return {"ok": True}


@app.post("/api/channels/{cid}/stop")
def stop(cid):
    manager.stop(cid)
    store.set_status(cid, "stopped")
    return {"ok": True}


@app.delete("/api/channels/{cid}")
def delete(cid):
    manager.remove(cid)
    store.del_channel(cid)
    manager.rebuild()
    return {"ok": True}


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


def _bare(p):
    for pre in ("+.", "*."):
        if p.startswith(pre):
            return p[len(pre):]
    return p


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


# 静态前端:把 app/static/ 整目录挂到根。上面的 /api/* 路由先注册、优先匹配;
# 其余路径(/css、/js、/*.html)由这里服务,html=True 让 "/" 返回 index.html。
app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
