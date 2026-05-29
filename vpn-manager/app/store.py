"""SQLite + 凭据字段级加密(Fernet)。主密钥存数据卷里权限锁死的文件(容器内读不到 macOS 钥匙串)。"""
import os, sqlite3
from cryptography.fernet import Fernet

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB = os.path.join(DATA_DIR, "vpnmgr.db")
KEYF = os.path.join(DATA_DIR, "master.key")


def _key():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(KEYF):
        fd = os.open(KEYF, os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, Fernet.generate_key())
        os.close(fd)
    with open(KEYF, "rb") as f:
        return f.read()


F = Fernet(_key())


def _c():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


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
            """
        )


def _row(r):
    d = dict(r)
    d.pop("password_enc", None)  # 永不把密码回传给前端
    return d


def add_channel(ch):
    pw = F.encrypt(ch["password"].encode()).decode() if ch.get("password") else ""
    with _c() as c:
        c.execute(
            """INSERT INTO channels(id,name,vpn_type,server,ec_ver,login_method,
               username,password_enc,vnc_password,mac,probe_url,status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ch["id"], ch["name"], ch["vpn_type"], ch["server"], ch["ec_ver"],
             ch["login_method"], ch["username"], pw, ch["vnc_password"], ch["mac"],
             ch["probe_url"], ch["status"]),
        )


def set_container(cid, container_id, novnc, status):
    with _c() as c:
        c.execute("UPDATE channels SET container_id=?,novnc_port=?,status=? WHERE id=?",
                  (container_id, novnc, status, cid))


def set_status(cid, status):
    with _c() as c:
        c.execute("UPDATE channels SET status=? WHERE id=?", (status, cid))


def get_channel(cid):
    with _c() as c:
        r = c.execute("SELECT * FROM channels WHERE id=?", (cid,)).fetchone()
        return _row(r) if r else None


def get_password(cid):
    with _c() as c:
        r = c.execute("SELECT password_enc FROM channels WHERE id=?", (cid,)).fetchone()
        return F.decrypt(r["password_enc"].encode()).decode() if r and r["password_enc"] else ""


def list_channels():
    with _c() as c:
        return [_row(r) for r in c.execute("SELECT * FROM channels").fetchall()]


def del_channel(cid):
    with _c() as c:
        c.execute("DELETE FROM channels WHERE id=?", (cid,))
        c.execute("DELETE FROM domains WHERE channel_id=?", (cid,))


def add_domain(cid, pattern):
    with _c() as c:
        c.execute("INSERT INTO domains(channel_id,pattern) VALUES(?,?)", (cid, pattern))


def list_domains(cid):
    with _c() as c:
        return [dict(r) for r in c.execute("SELECT * FROM domains WHERE channel_id=?", (cid,)).fetchall()]


def all_domains():
    with _c() as c:
        return [dict(r) for r in c.execute("SELECT * FROM domains").fetchall()]
