import os
import uuid
import random
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

import store
import manager

HERE = os.path.dirname(__file__)
MIHOMO_HOST_PORT = os.environ.get("MIHOMO_HOST_PORT", "?")

app = FastAPI(title="VPN 通道管理器 (demo)")
store.init()


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/channels")
def channels():
    out = []
    for c in store.list_channels():
        c["domains"] = store.list_domains(c["id"])
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
    url = (f"http://127.0.0.1:{ch['novnc_port']}/vnc.html"
           f"?path=websockify&autoconnect=true&resize=remote&password={ch['vnc_password']}")
    return {"url": url}


@app.get("/api/channels/{cid}/status")
def status(cid):
    ch = store.get_channel(cid)
    if not ch:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok = manager.probe(ch)
    new = "logged_in" if ok else ("running" if ch["status"] == "logged_in" else ch["status"])
    store.set_status(cid, new)
    return {"status": new, "connected": ok}


@app.post("/api/channels/{cid}/domains")
async def add_domain(cid, req: Request):
    b = await req.json()
    pat = (b.get("pattern") or "").strip()
    if pat:
        store.add_domain(cid, pat)
    code = manager.rebuild()
    return {"reload_status": code, "domains": store.list_domains(cid)}


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


@app.get("/api/clash-snippet", response_class=PlainTextResponse)
def clash_snippet():
    doms = store.all_domains()
    lines = [
        "# ① 在你现有 Clash 的 proxies: 下加这个节点",
        "proxies:",
        "  - name: vpn-router",
        "    type: socks5",
        "    server: 127.0.0.1",
        f"    port: {MIHOMO_HOST_PORT}",
        "",
        "# ② 在 rules: 顶部加这些(命中的域名交给本工具分流;no-resolve 让域名在 VPN 侧解析)",
    ]
    if doms:
        lines += [f"  - DOMAIN-SUFFIX,{d['pattern']},vpn-router,no-resolve" for d in doms]
    else:
        lines.append("  # (还没绑定任何域名)")
    return "\n".join(lines)
