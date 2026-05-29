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
