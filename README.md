# 🧮 calc-otel

**用最企业级的依赖，跑最生草的 1+1=2，然后给它配上区块链、BI 看板、指纹采集和系统通知**

> *这已经不是"用高射炮打蚊子"了，这是用粒子对撞机去轰一颗草履虫，然后为了看清楚撞击轨迹，现场手搓了一台电子显微镜。*

[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-1.24+-blueviolet)](https://opentelemetry.io/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 📖 这到底是个啥

现代云原生可观测性的全套工业级依赖——OpenTelemetry API、SDK、OTLP HTTP 导出器、requests 自动插桩、Flask 内置查看器——**去追踪一次 1+1=2 的调用链路**。

在 `calc_client.py` 里，每一个按键都被拆成了一个 click span，底下挂着 HTTP POST、json.parse、ui.parse 三个子 span。一次 1+1 算下来，**27 个 span**，横跨 client、internal、server 多种 span kind。

还非要在 **ARM32 安卓手机**上，把 Rust 的 611 个包编译出来。

### 作者评价

> 这已经不是"用高射炮打蚊子"了，这是用粒子对撞机去轰一颗草履虫，然后为了看清楚撞击轨迹，现场手搓了一台电子显微镜。
>如何评价升级:这是给草履虫建了一整个粒子对撞机产业园，还配套了发电站、监控中心、区块链结算系统和浏览器指纹采集！
---

## 🔗 依赖

| 项目 | 说明 |
|---|---|
| [otel-gui](https://github.com/metafab/otel-gui) | 轻量级、零配置的 OpenTelemetry 追踪查看器 |
| [arm32-rolldown-termux](https://github.com/chinartcn/arm32-rolldown-termux) | ARM32 Rolldown 编译方案 |
| [ai-v9-artificialretard](https://github.com/Chinartcn/ai-v9-artificialretard) | 伪装成 AI 的状态机，包含计算器 😂 |
| [uv](https://github.com/astral-sh/uv) | 极速 Python 包管理器 |
| [tmux]pkg install tmux|./dev.sh|
### Python 依赖

```toml
[project]
name = "calc-otel"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "requests>=2.31",
    "flask>=3.0",
    "opentelemetry-api>=1.24",
    "opentelemetry-sdk>=1.24",
    "opentelemetry-exporter-otlp-proto-http>=1.24",
    "opentelemetry-instrumentation-requests>=0.45b0",
]

[tool.uv]
package = false
```

---

## 📁 目录结构

```
calc_client/
├── pyproject.toml          # uv 项目配置
├── README.md               # 本文件
├── calc_client.py          # 主程序：计算器 + OTel 追踪 + HTTP API
├── app.py                  # Web 前端网关 + 浏览器指纹收集
└── sdk.py                  # 密码登录的数据看板

otel-gui/                   # 追踪查看器
├── package.json
├── vite.config.ts
└── src/
```

---

## 🚀 食用方法

### 第一步：启动 otel-gui

```bash
# 请先克隆 otel-gui
git clone https://github.com/metafab/otel-gui.git
cd otel-gui
pnpm install
pnpm dev
```

预期输出：

```
  VITE v8.0.16  ready in 8795 ms

  ➜  Local:   http://localhost:4318/
  ➜  Network: use --host to expose
  ➜  press h + enter to show help
```

> **注意**：otel-gui 默认监听 `4318`，这正是 OTLP/HTTP 的标准端口。Zero config，开箱即用。

####tmux ./dev.sh
启动**calc_client**与**web服务**
>必须先安装`*pkg install tmux*`

```bash
chmod +x
./dev.sh
```
--**`启动后页面**
```bash
── 0 calc_client ─────────────────────────────────────┬── 1 app ──────
cd '/data/data/com.termux/files/home/calc_client' && s│7.018:40 [0/26]
ource '/data/data/com.termux/files/home/calc_client/.v│uth=off)
env/bin/activate' && uv run calc_client.py --api-host │[frontend] fp d
0.0.0.0 --pow-difficulty 3                            │b: /data/data/c
➜  calc_client cd '/data/data/com.termux/files/home/ca│om.termux/files
lc_client' && source '/data/data/com.termux/files/home│/home/calc_cli$
/calc_client/.venv/bin/activate' && uv run calc_client│nt/fingerprints
.py --api-host 0.0.0.0 --pow-difficulty 3             │.db
[viewer] http://127.0.0.1:8899/                       │ * Serving Flas
 * Serving Flask app 'trace_viewer'                   │k app 'app'
 * Debug mode: off                                    │ * Debug mode:
[api] http://0.0.0.0:8900/                            │off
 * Serving Flask app 'calc_api'                       │WARNING: This i
 * Debug mode: off                                    │s a development
[stats] DB: /data/data/com.termux/files/home/calc_clie│ server. Do not
nt/calc_stats.db                                      │ use it in a pr
[cache] TTL=300s  max_size=1000                       │oduction deploy
[chain] height=4  difficulty=1  supply=3.50 CALC  cach│ment. Use a pro
ed_blocks=1  市值=$0.0000000350                       │duction WSGI se
                                                      │rver instead.
  对外 API：                                          │ * Running on a
    POST http://0.0.0.0:8900/api/calc                 │ll addresses (0
    GET  http://0.0.0.0:8900/api/chain                │.0.0.0)
    GET  http://0.0.0.0:8900/api/chain/stats          │ * Running on h
    GET  http://0.0.0.0:8900/api/chain/verify         │ttp://127.0.0.1
    GET  http://0.0.0.0:8900/api/wallets              │:8800
    GET  http://0.0.0.0:8900/api/transactions         │ * Running on h
                                                      │ttp://10.30.168
输入表达式，空行退出。                                │.177:8800
命令：:chain  :verify  :wallet  :pow <n>  :export  :qu│Press CTRL+C to
it                                                    │ quit
                                                      │
> 1+1                                                 ├── 2 sdk ──────
  1+1 = 2   [307 ms]   trace=3f9b04c7                 │/.venv/bin/acti
  viewer: http://127.0.0.1:8899/trace/3f9b04c72d77cc8b│vate' && uv run
7b7129926bdd7772                                      │ sdk.py
> :chain                                              │➜  calc_client
  高度=4  难度=1  总工作=136  缓存块=1                │cd '/data/data/
  供应=3.5000 CALC  钱包=1  市值=$0.0000000350        │com.termux/file
    #4 0c0b3810e0a7… 1+1=2 d=1 nonce=18 [缓存] by web:│s/home/calc_cli
efbc7293                                              │ent' && source
    #3 0fc937459239… 1+1=2 d=1 nonce=54 by web:efbc729│'/data/data/com
3                                                     │.termux/files/h
    #2 0476f08da3d9… 999/45=22.2 d=1 nonce=42 by web:e│ome/calc_client
fbc7293                                               │/.venv/bin/acti
    #1 064270367b57… 999/455=2.195604395604396 d=1 non│vate' && uv run
ce=18 by web:efbc7293                                 │ sdk.py
    #0 a6752dbb5af6… GENESIS=CALC d=1 nonce=0 by syste│[sdk] http://12
m                                                     │7.0.0.1:8700/
> 1                                                   │[sdk] calc API:
  1 = 1   [128 ms]   trace=a9f8e12f                   │ http://127.0.0
  viewer: http://127.0.0.1:8899/trace/a9f8e12f379c54b2│.1:8900
ade16f13e97826c5                                      │[sdk] 密码文件:
>                                                     │ /data/data/com
                                                      │.termux/files/h
                                                      │ome/calc_client
                                                      │/admin.pw
                                                      │[sdk] 如需改密
                                                      │码，删除 admin.
                                                      │pw 或设 ADMIN_P
                                                      │ASSWORD
                                                      │ * Serving Flas
                                                      │k app 'sdk'
                                                      │ * Debug mode:
                                                      │off
                                                      │
 calc | ca0:[tmux]*                                      COPY | 19:05
```

### 第二步：启动 calc_client

**另一个窗口**：

```bash
cd calc_client
uv run calc_client.py --api-host 0.0.0.0 --pow-difficulty 3 --otlp http://127.0.0.1:4318/v1/traces
```

预期输出：

```
[viewer] http://127.0.0.1:8899/
[api] http://0.0.0.0:8900/
[otel] OTLP 导出 → http://127.0.0.1:4318/v1/traces
[stats] DB: /path/to/calc_stats.db
[cache] TTL=300s  max_size=1000
[chain] height=0  difficulty=3  supply=0.00 CALC  市值=$0.0000000000
```

### 第三步：启动 ai-v9-artificialretard

```bash
cd ai-v9-artificialretard
chmod +x start.sh
./start.sh start
```

### 第四步：启动 Web 服务

```bash
python3 app.py    # 前端网关 :8800
python3 sdk.py    # 数据看板 :8700
```

---

## 🌐 服务总览

| 服务 | 端口 | 说明 |
|---|---|---|
| `otel-gui` | 4318 | OTLP 追踪查看器 |
| `calc_client` viewer | 8899 | 内置 trace 火焰图 |
| `calc_client` API | 8900 | 计算 API + 链数据 |
| `app.py` 前端 | 8800 | Web 计算器 + 指纹收集 |
| `sdk.py` 看板 | 8700 | 密码登录的数据看板 |
| `ai-v9` 状态机 | 5002 | 伪装的 AI 计算器 |

---

## 🧪 验证一下"企业级"

算一次 `1+1`，去 `http://localhost:4318` 看 trace：

```
Trace 7de6a8f1b2c3d4e5f6a7b8c9d0e1f2a3
├── api.calc                    SERVER    215 ms
│   ├── pow                     INTERNAL   47 ms    ← PoW 挖矿
│   ├── calc                    INTERNAL  120 ms
│   │   ├── start               INTERNAL   25 ms
│   │   │   ├── HTTP POST       CLIENT     18 ms
│   │   │   ├── json.parse      INTERNAL    3 ms
│   │   │   └── ui.parse        INTERNAL    2 ms
│   │   ├── C                   INTERNAL   18 ms
│   │   ├── 1                   INTERNAL   17 ms
│   │   ├── +                   INTERNAL   16 ms
│   │   ├── 1                   INTERNAL   17 ms
│   │   └── =                   INTERNAL   52 ms
│   └── notify                  INTERNAL    3 ms
```

**总共 27 个 span**，横跨 client、internal、server 三种 span kind。

---

## ⛓ CALC 链（行为艺术模块）

每次计算都会生成一个 PoW 区块，挂在链上：

```
Block #0 (GENESIS)
  hash = 0d99d20f...
    │
Block #1  expr="1+1" result="2" nonce=16 d=1
  prev_hash = Block#0.hash
  hash = 000a8b7c...
    │
Block #2  expr="1+1" result="2" nonce=6231 d=3 (缓存命中)
  prev_hash = Block#1.hash
  hash = 000f3e9a...
```

- **PoW**：SHA256 前导零难度，动态调整
- **CALC 代币**：每次计算奖励 1-3 CALC，缓存命中减半
- **不可篡改**：改任何一个字段，整条链断

---

## ⚠️ 已知问题

- **ARM32 编译**：`arm32-rolldown-termux` 是为了解决 Termux/Android 上 `pnpm dev` 因 Rolldown N-API 绑定缺失而 SIGILL 崩溃的问题
- **PoW 速度**：ARM32 上难度 3 约 30-80ms，难度 5 约 5-10 秒
- **数据库迁移**：从旧版本升级时，如果 hash 算法变了，需要 `rm calc_stats.db` 重建

---



**在 Termux 里，用粒子对撞机轰草履虫。**