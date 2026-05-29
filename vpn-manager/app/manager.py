"""容器编排 + mihomo 热加载 + SOCKS5 探活。"""
import os
import requests
import yaml
import docker

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
    """从后端经该通道的 SOCKS5 访问内网探测地址,通=真登录上(socks5h 远程解析)。"""
    if not ch.get("probe_url"):
        return False
    px = f"socks5h://vpn-{ch['id']}:1080"
    try:
        r = requests.get(ch["probe_url"], proxies={"http": px, "https": px}, timeout=6)
        return r.status_code < 500
    except Exception:
        return False


def rebuild():
    """按当前所有通道+域名重写 mihomo 配置并热加载(force reload,不断现有连接)。"""
    chs = store.list_channels()
    doms = store.all_domains()
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
    rules = [f"DOMAIN-SUFFIX,{d['pattern']},ch-{d['channel_id']}" for d in doms]
    rules.append("MATCH,DIRECT")
    base["rules"] = rules

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
