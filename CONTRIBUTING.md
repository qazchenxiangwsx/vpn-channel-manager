# 开发说明

> 单人自用项目。本文件是给「未来的自己 / 接手的 Agent」的最小约定。代码现状、命门与下一步见 **CLAUDE.md**;完整设计意图见 **VPN通道管理器-落地方案.md**。

## 环境

- macOS / 一台常开小主机,装 Docker(已实测 Docker 29.3.1 / API 1.54 / arm64)。
- 后端依赖只在 Docker 镜像里,系统 Python 没装 fastapi——**本机无法直接 `uvicorn` 起 app**,要起整栈走 compose。

## 跑起来

```bash
cd vpn-manager && ./start.sh          # 全栈:gen_env → 渲染 mihomo 配置 → compose up
# 管理界面 http://127.0.0.1:${UI_PORT}(端口见 vpn-manager/.env);停止 docker compose down
```

只改前端、不起后端:

```bash
cd vpn-manager/app/static && python3 -m http.server 8080
```

## 测试

单元测试用 pytest,依赖独立于运行镜像(`tests/conftest.py` 在 import 前注入测试用 env,SQLite 落临时目录,manager 的容器 / 探活等副作用全 monkeypatch 掉):

```bash
cd vpn-manager
pip install -r tests/requirements-dev.txt
pytest tests/                          # test_api / test_clash / test_manager / test_store
```

栈冒烟(需 Docker,起真 compose、断言关键端点 + mihomo 热加载,不含真 VPN 登录):

```bash
cd vpn-manager && ./tests/smoke.sh
```

## 约定

- **改 API 必须同步改 README 的 API 表**,并以 `vpn-manager/app/main.py` 为唯一事实源。
- 提交信息沿用 `type(scope): 摘要`(feat / fix / test / docs / chore)。
- 前端设计系统见 CLAUDE.md 末尾的「设计系统」一节,保持 Neutral Modern,不要 AI-slop。

## 绝不能破坏的命门(详见 CLAUDE.md)

1. 登录成功的唯一判据 = 后端 SOCKS5 探活,**不是**「VNC 连上了」。
2. 配置热加载、绝不断连(`PUT /configs?force=true`,不重启 mihomo)。
3. 所有 host 端口只绑 `127.0.0.1`,永不 `0.0.0.0`。
4. 凭据 Fernet 加密落库,接口永不回传密文;`master.key` 权限 0600。
