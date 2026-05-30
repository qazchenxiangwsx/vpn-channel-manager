"""容器编排 + mihomo 热加载 + SOCKS5 探活。"""
import os
import time
import requests
import yaml
import docker
from datetime import datetime, timezone

import store

VPN_NET = os.environ["VPN_NET"]
CTRL = os.environ["MIHOMO_CTRL_URL"]
SECRET = os.environ["MIHOMO_SECRET"]
CFG = os.environ.get("MIHOMO_CONFIG_PATH", "/cfg/config.yaml")

dc = docker.from_env()


def create_channel(ch, vnc_pwd):
    """起一个 VPN 容器:SOCKS5(1080)只在 Docker 内网暴露,noVNC(8080)由 Docker 自动分配高位随机端口映射到 127.0.0.1。"""
    if ch["vpn_type"] == "atrust":
        image = "hagb/docker-atrust:latest"
    else:
        image = f"hagb/docker-easyconnect:{ch['ec_ver'] or '7.6.3'}"

    env = {"USE_NOVNC": "1", "PASSWORD": vnc_pwd, "EXIT": "", "FAKE_HWADDR": ch["mac"]}
    if ch["ec_ver"]:
        env["EC_VER"] = ch["ec_ver"]

    kw = dict(
        image=image,
        name=f"vpn-{ch['id']}",
        detach=True,
        devices=["/dev/net/tun:/dev/net/tun:rwm"],
        cap_add=["NET_ADMIN"],
        environment=env,
        hostname=ch["id"],
        volumes={f"vpndata-{ch['id']}": {"bind": "/root", "mode": "rw"}},
        ports={"8080/tcp": ("127.0.0.1", None)},   # None = Docker 自动分配空闲高位端口
        network=VPN_NET,
        restart_policy={"Name": "unless-stopped"},
    )
    if ch["vpn_type"] == "atrust":
        kw["sysctls"] = {"net.ipv4.conf.default.route_localnet": "1"}
        env["DISABLE_PKG_VERSION_XML"] = "1"

    try:
        dc.containers.get(f"vpn-{ch['id']}").remove(force=True)
    except docker.errors.NotFound:
        pass

    c = dc.containers.run(**kw)
    c.reload()
    novnc = int(c.ports["8080/tcp"][0]["HostPort"])
    return c.id, novnc


def ensure_novnc_bridge(cid):
    """确保 noVNC 可用:用一个自起的 websockify 顶在容器 8080(= 映射到 host 的端口)。

    hagb 镜像自带的 noVNC 前端在 arm64 上不稳:websockify(8082)因 `su -s /bin/sh`
    "Permission denied" 起不来;tinyproxy(8080)偶发因 /etc/tinyproxy-novnc.conf
    没生成("Read-only file system")而不启动 → noVNC「无法连接到服务器」甚至
    「未发送任何数据」。这里以 root 直接拉起一个全功能 websockify(--web 同时服务
    noVNC 静态页 + WS→VNC 桥)占住 8080,绕开整条易碎链路。幂等:已在跑则跳过。
    """
    try:
        c = dc.containers.get(f"vpn-{cid}")
        start = (
            "pgrep -f 'websockify --web' >/dev/null 2>&1 && exit 0; "
            "pkill -f tinyproxy-novnc 2>/dev/null; "
            "exec websockify --web /usr/local/share/novnc 0.0.0.0:8080 127.0.0.1:5901 "
            ">/tmp/novnc-bridge.log 2>&1"
        )
        c.exec_run(["sh", "-c", start], user="root", detach=True)
        # 等 8080 起来再返回,避免前端 iframe 抢跑拿到空响应(ERR_EMPTY_RESPONSE)
        for _ in range(20):
            rc, _o = c.exec_run(["sh", "-c", "ss -tln 2>/dev/null | grep -q :8080"])
            if rc == 0:
                break
            time.sleep(0.2)
    except Exception:
        pass


def stop(cid):
    try:
        dc.containers.get(f"vpn-{cid}").stop()
    except docker.errors.NotFound:
        pass


def start(cid):
    try:
        dc.containers.get(f"vpn-{cid}").start()
    except docker.errors.NotFound:
        pass


def remove(cid):
    try:
        dc.containers.get(f"vpn-{cid}").remove(force=True)
    except docker.errors.NotFound:
        pass


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
