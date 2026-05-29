/* 示例数据 — 贴合《VPN通道管理器-落地方案》。纯演示用，非真实凭据。
 * 端口规则：socks = 10800 + 序号，novnc = 18080 + 序号（绑 127.0.0.1）。 */
window.VPN = {
  system: {
    mihomoStatus: "running",     // 本工具第二跳 mihomo 子进程
    mihomoPort: 7899,            // mixed-port，给你的 Clash 接
    controller: "127.0.0.1:9090",
    clashLinked: true,           // 你的 Clash 是否已粘节点+订阅
    uiPort: 51847,               // 管理界面高位随机端口
    boundIp: "127.0.0.1",
  },
  channels: [
    {
      id: "a1f3c8d2",
      name: "维度 CRM（客户A）",
      vpn_type: "easyconnect",
      ec_ver: "7.6.3",
      server: "https://sslvpn.weidu-crm.com",
      login_method: "password",
      username: "ops_acme",
      status: "logged_in",
      socks_port: 10800,
      novnc_port: 18080,
      mac: "02:a4:1f:9c:6b:30",
      volume_name: "vpn-a1f3c8d2-root",
      vnc_password: "7f3a9c21",
      probe_url: "http://crm.weidu.内网/login",
      latency_ms: 42,
      uptime: "3天 6小时",
      domains: [
        { pattern: "+.weidu-crm.com", enabled: true },
        { pattern: "crm.weidu.内网", enabled: true },
      ],
      ips: [
        { pattern: "10.20.0.0/16", enabled: true },     // 内网数据库段（无对外域名）
        { pattern: "192.168.30.40/32", enabled: true }, // 单台文件服务器
      ],
    },
    {
      id: "b7e2a190",
      name: "信服 OA（客户B）",
      vpn_type: "atrust",
      ec_ver: null,
      server: "https://vpn.bservice.cn",
      login_method: "interactive",
      username: "",
      status: "running",          // 容器起来、SOCKS5 监听，但还没登录
      socks_port: 10801,
      novnc_port: 18081,
      mac: "02:c1:8d:44:e7:5a",
      volume_name: "vpn-b7e2a190-root",
      vnc_password: "a09b22ff",
      probe_url: "http://oa.bservice.内网/portal",
      latency_ms: null,
      uptime: "2分钟",
      domains: [],
      ips: [],
    },
    {
      id: "c3d9f604",
      name: "财税门户（客户C）",
      vpn_type: "easyconnect",
      ec_ver: "7.6.7",
      server: "https://ec.taxgroup.com.cn",
      login_method: "interactive",
      username: "",
      status: "logged_in",
      socks_port: 10802,
      novnc_port: 18082,
      mac: "02:5b:77:21:af:13",
      volume_name: "vpn-c3d9f604-root",
      vnc_password: "33ce81b0",
      probe_url: "http://fp.taxgroup.内网/api/ping",
      latency_ms: 88,
      uptime: "11小时",
      domains: [
        { pattern: "+.tax-portal.cn", enabled: true },
        { pattern: "fp.taxgroup.内网", enabled: true },
        { pattern: "sso.taxgroup.com.cn", enabled: false },
      ],
      ips: [
        { pattern: "172.16.8.0/24", enabled: true },  // 财税内网 ERP 段
        { pattern: "10.99.1.5/32", enabled: false },  // 灰度库，暂停分流
      ],
    },
    {
      id: "d8a04e7b",
      name: "物流中台（客户D）",
      vpn_type: "easyconnect",
      ec_ver: "7.6.3",
      server: "https://sslvpn.cargo-mid.com",
      login_method: "password",
      username: "monitor",
      status: "stopped",
      socks_port: 10803,
      novnc_port: 18083,
      mac: "02:9f:0a:3e:cc:88",
      volume_name: "vpn-d8a04e7b-root",
      vnc_password: "be71d2a4",
      probe_url: "http://tms.cargo.内网/health",
      latency_ms: null,
      uptime: "—",
      domains: [{ pattern: "+.cargo-mid.com", enabled: true }],
      ips: [{ pattern: "10.50.0.0/16", enabled: true }],
    },
  ],

  // 监控页：最近实时连接（域名 → 命中通道 → SOCKS5 出口）
  connections: [
    { host: "crm.weidu.内网:443", rule: "+.weidu-crm.com", chain: "a1f3c8d2", node: "chanA-socks", up: "12.4 KB", down: "318 KB", dl: 41 },
    { host: "fp.taxgroup.内网:443", rule: "fp.taxgroup.内网", chain: "c3d9f604", node: "chanC-socks", up: "3.1 KB", down: "92 KB", dl: 86 },
    { host: "static.weidu-crm.com:443", rule: "+.weidu-crm.com", chain: "a1f3c8d2", node: "chanA-socks", up: "1.9 KB", down: "204 KB", dl: 44 },
    { host: "sso.taxgroup.com.cn:443", rule: "MATCH", chain: "—", node: "DIRECT", up: "0.8 KB", down: "5 KB", dl: 12 },
    { host: "tax-portal.cn:443", rule: "+.tax-portal.cn", chain: "c3d9f604", node: "chanC-socks", up: "5.2 KB", down: "61 KB", dl: 90 },
    // 按目标 IP 命中（无域名的内网主机）：no-resolve 直接匹配目的地址
    { host: "10.20.4.12:1433", rule: "10.20.0.0/16", chain: "a1f3c8d2", node: "chanA-socks", up: "8.7 KB", down: "146 KB", dl: 43 },
    { host: "172.16.8.30:445", rule: "172.16.8.0/24", chain: "c3d9f604", node: "chanC-socks", up: "2.3 KB", down: "77 KB", dl: 88 },
  ],
};
