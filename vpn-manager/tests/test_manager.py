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
