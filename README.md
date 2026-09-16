**用最企业级的依赖，跑最生草的 1+1=2。**
***依旧整活***

## 依赖

- [otel-gui](https://github.com/chinartcn/otel-gui) — OTLP 追踪查看器
- [arm32-rolldown-termux](https://github.com/chinartcn/arm32-rolldown-termux) — ARM32 Rolldown 
- [ai-v9-artificialretard](https://github.com/Chinartcn/ai-v9-artificialretard) --伪装成AI的状态机包含计算器😂
- uv包管理器
```
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
##作者评价
**这已经不是“用高射炮打蚊子”了，这是用粒子对撞机去轰一颗草履虫，然后为了看清楚撞击轨迹，现场手搓了一台电子显微镜。**
**现代云原生可观测性的全套工业级依赖——OpenTelemetry API、SDK、OTLP HTTP 导出器、requests 自动插桩、Flask 内置查看器——去追踪一次 1+1=2 的调用链路。**
**在 calc_client.py 里，每一个按键都被拆成了一个 click span，底下挂着 HTTP POST、json.parse、ui.parse 三个子 span。一次 1+1 算下来，27 个 span，横跨 client、internal、producer、consumer 四种 span kind。**
**还非要在 ARM32 安卓手机上，把 Rust 的 611 个包编译出来**
[![Rolldown-arm32](https://github.com/chinartcn/arm32-rolldown-termux)]

###目录结构
calc_client:

otel-gui/
README.md, pyproject.toml, calc_client.py

##食用方法
克隆仓库

```bash
#请先克隆otel-gui
cd otel-gui
pnpm dev
```
*另一个窗口*
```bash
cd calc_client
uv run calc_client.py --otlp http://127.0.0.1:4318/v1/traces
```
#"uv run calc_client.py --otlp http://127.0.0.1:4318/v1/traces"详细看

```bash
➜  otel-gui git:(main) pnpm dev
$ vite dev

  VITE v8.0.16  ready in 8795 ms

  ➜  Local:   http://localhost:4318/
  ➜  Network: use --host to expose
  ➜  press h + enter to show help
```
***的"Local"***

同时克隆![ai-v9-artificialretard](https://github.com/Chinartcn/ai-v9-artificialretard)

```bash
cd ai-v9-artificialretard
chmod +x start.sh
./start.sh start 
```