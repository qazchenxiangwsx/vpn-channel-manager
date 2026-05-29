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
