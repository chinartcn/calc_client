#!/usr/bin/env python3
# calc_client.py — OTel 追踪 + 内置查看服务 + OTLP 导出
import os
import re
import sys
import json
import time
import html
import logging
import argparse
import threading

import requests

# ---------- OpenTelemetry ----------
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor, SpanExporter, SpanExportResult,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

try:
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    HAS_REQ_INSTR = True
except ImportError:
    HAS_REQ_INSTR = False

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    HAS_OTLP = True
except ImportError:
    HAS_OTLP = False

try:
    from flask import Flask, jsonify
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


BASE = "http://127.0.0.1:5002/v1/chat/completions"
MODEL = "ai-v9-artificialretard"
OP_MAP = {"+": "+", "-": "-", "*": "×", "/": "÷", ".": "."}
KIND_COLOR = {
    "CLIENT": "#4a90d9",
    "INTERNAL": "#7ab87a",
    "SERVER": "#d97a4a",
    "PRODUCER": "#b07ad9",
    "CONSUMER": "#d9b07a",
}
PROPAGATOR = TraceContextTextMapPropagator()

# OTLP kind 数字映射
_KIND_MAP = {
    "INTERNAL": 1,
    "SERVER": 2,
    "CLIENT": 3,
    "PRODUCER": 4,
    "CONSUMER": 5,
}

logging.getLogger("werkzeug").setLevel(logging.ERROR)


# ============ 内存 exporter ============
class MemoryExporter(SpanExporter):
    def __init__(self):
        self.spans = []
        self._lock = threading.Lock()

    def export(self, spans):
        with self._lock:
            self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def get_traces(self):
        with self._lock:
            spans = list(self.spans)
        traces = {}
        for s in spans:
            tid = format(s.context.trace_id, "032x")
            traces.setdefault(tid, []).append(s)
        return traces

    def clear(self):
        with self._lock:
            self.spans.clear()


MEM = MemoryExporter()


# ============ OTLP 导出 ============
def _attr_value(v):
    """把 Python 值转成 OTLP AnyValue"""
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_attr_value(x) for x in v]}}
    return {"stringValue": str(v)}


def _attrs_to_otlp(attrs):
    return [{"key": k, "value": _attr_value(v)} for k, v in (attrs or {}).items()]


def _span_to_otlp(s):
    """ReadableSpan → OTLP span JSON"""
    d = {
        "traceId": format(s.context.trace_id, "032x"),
        "spanId": format(s.context.span_id, "016x"),
        "name": s.name,
        "kind": _KIND_MAP.get(s.kind.name, 1),
        "startTimeUnixNano": str(s.start_time),
        "endTimeUnixNano": str(s.end_time),
        "attributes": _attrs_to_otlp(s.attributes),
        "status": {"code": 1 if s.status.status_code.name == "OK" else 2},
    }
    if s.parent is not None and s.parent.is_valid:
        d["parentSpanId"] = format(s.parent.span_id, "016x")
    # 事件（异常等）
    if s.events:
        d["events"] = [{
            "name": e.name,
            "timeUnixNano": str(e.timestamp),
            "attributes": _attrs_to_otlp(e.attributes),
        } for e in s.events]
    return d


def export_otlp(exporter, path):
    """把内存里的所有 trace 写成 OTLP JSON"""
    traces = exporter.get_traces()
    all_spans = []
    resource_attrs = []

    for spans in traces.values():
        if spans and not resource_attrs:
            resource_attrs = _attrs_to_otlp(spans[0].resource.attributes)
        for s in spans:
            all_spans.append(_span_to_otlp(s))

    payload = {
        "resourceSpans": [{
            "resource": {"attributes": resource_attrs},
            "scopeSpans": [{
                "scope": {"name": "calc_client", "version": "1.0.0"},
                "spans": all_spans,
            }],
        }],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return len(traces), len(all_spans)


# ============ OTel 初始化 ============
def setup_otel(otlp_endpoint=None, service_name="calc-client"):
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(MEM))

    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint and HAS_OTLP:
        provider.add_span_processor(
            SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        print(f"[otel] OTLP 导出 → {endpoint}")

    otel_trace.set_tracer_provider(provider)

    if HAS_REQ_INSTR:
        RequestsInstrumentor().instrument(tracer_provider=provider)

    return otel_trace.get_tracer("calc_client")


# ============ UI 解析 ============
def parse_ui(content):
    m = re.search(r"```kai-ui\s*(\{.*\})\s*```", content, re.S)
    return json.loads(m.group(1)) if m else json.loads(content)


def find_event(ui, label):
    if not isinstance(ui, dict):
        return None
    if ui.get("type") == "button" and ui.get("label") == label:
        return (ui.get("action") or {}).get("event")
    for child in ui.get("children", []) or []:
        ev = find_event(child, label)
        if ev:
            return ev
    return None


def get_display(ui):
    for c in ui.get("children", []) or []:
        if c.get("type") == "text":
            return c.get("value")
    return None


def fmt_ms(ns):
    ms = ns / 1e6
    return f"{ms:.1f} ms" if ms < 1000 else f"{ms/1000:.2f} s"


# ============ 查看服务 ============
def _parent_hex(s):
    p = s.parent
    if p is None or not p.is_valid:
        return ""
    return format(p.span_id, "016x")


def _render_trace_html(tid, spans):
    if not spans:
        return "<p>空 trace</p>"
    spans_sorted = sorted(spans, key=lambda s: (s.start_time, -s.end_time))
    min_start = min(s.start_time for s in spans)
    max_end = max(s.end_time for s in spans)
    total = max(max_end - min_start, 1)
    by_id = {s.context.span_id: s for s in spans}

    def depth(s):
        d = 0
        p = s.parent
        while p is not None and p.is_valid and p.span_id in by_id:
            d += 1
            p = by_id[p.span_id].parent
        return d

    rows = []
    for s in spans_sorted:
        left = (s.start_time - min_start) / total * 100
        width = max((s.end_time - s.start_time) / total * 100, 0.3)
        dur = fmt_ms(s.end_time - s.start_time)
        color = KIND_COLOR.get(s.kind.name, "#888")
        attrs = " ".join(
            f"{k}={v}" for k, v in s.attributes.items() if len(str(v)) < 40
        )
        rows.append(f"""
          <div class="row" style="padding-left:{depth(s)*22}px">
            <div class="label">
              <b>{html.escape(s.name)}</b>
              <span class="id">{format(s.context.span_id,'016x')[:8]}</span>
              <span class="kind" style="color:{color}">{s.kind.name.lower()}</span>
            </div>
            <div class="bar-wrap">
              <div class="bar" style="left:{left:.3f}%;width:{width:.3f}%;background:{color}">{dur}</div>
            </div>
            <div class="attrs">{html.escape(attrs)}</div>
          </div>
        """)
    return f"""
    <div class="meta">trace {tid[:16]} · 总 {fmt_ms(max_end-min_start)} · {len(spans)} spans</div>
    {''.join(rows)}
    """


CSS = """
body{font-family:ui-monospace,Menlo,monospace;background:#0f1115;color:#d8dee9;margin:0;padding:20px}
h1{color:#88c0d0;font-size:18px}
a{color:#88c0d0;text-decoration:none} a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 10px;border-bottom:1px solid #2e3440;text-align:left}
th{color:#81a1c1;font-weight:600}
.row{position:relative;padding:4px 0;border-bottom:1px solid #1c2027}
.label{font-size:12px;margin-bottom:2px}
.id{color:#616e88;margin-left:6px} .kind{margin-left:8px;font-size:11px}
.attrs{color:#616e88;font-size:11px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-wrap{position:relative;height:18px;background:#1c2027;border-radius:3px;overflow:hidden}
.bar{position:absolute;height:100%;top:0;border-radius:3px;color:#fff;font-size:11px;line-height:18px;padding-left:6px;white-space:nowrap;overflow:hidden}
.meta{color:#81a1c1;font-size:12px;margin:12px 0}
"""


def build_viewer_app(exporter):
    app = Flask("trace_viewer")

    @app.route("/")
    def index():
        traces = exporter.get_traces()
        rows = []
        for tid, spans in traces.items():
            min_s = min(s.start_time for s in spans)
            max_e = max(s.end_time for s in spans)
            roots = [s for s in spans if not (s.parent and s.parent.is_valid)]
            name = roots[0].name if roots else "?"
            rows.append(f"""
              <tr>
                <td><a href="/trace/{tid}">{tid[:16]}</a></td>
                <td>{html.escape(name)}</td>
                <td>{len(spans)}</td>
                <td>{fmt_ms(max_e-min_s)}</td>
              </tr>
            """)
        body = f"""
        <h1>Traces ({len(traces)})</h1>
        <table>
          <tr><th>Trace ID</th><th>Root</th><th>Spans</th><th>Duration</th></tr>
          {''.join(rows) if rows else '<tr><td colspan=4>还没有 trace</td></tr>'}
        </table>
        <p style="color:#616e88;font-size:12px;margin-top:20px">刷新页面查看最新数据</p>
        """
        return f"<!doctype html><meta charset=utf-8><style>{CSS}</style>{body}"

    @app.route("/trace/<tid>")
    def trace_view(tid):
        traces = exporter.get_traces()
        matched = [k for k in traces if k.startswith(tid)]
        if not matched:
            return f"trace {tid} not found", 404
        full_tid = matched[0]
        spans = traces[full_tid]
        body = f"""
        <h1><a href="/">←</a> trace {full_tid[:16]}</h1>
        {_render_trace_html(full_tid, spans)}
        """
        return f"<!doctype html><meta charset=utf-8><style>{CSS}</style>{body}"

    @app.route("/api/traces")
    def api_traces():
        out = []
        for tid, spans in exporter.get_traces().items():
            min_s = min(s.start_time for s in spans)
            max_e = max(s.end_time for s in spans)
            out.append({
                "trace_id": tid,
                "duration_ms": (max_e - min_s) / 1e6,
                "span_count": len(spans),
            })
        return jsonify(out)

    @app.route("/api/trace/<tid>")
    def api_trace(tid):
        traces = exporter.get_traces()
        matched = [k for k in traces if k.startswith(tid)]
        if not matched:
            return jsonify({"error": "not found"}), 404
        spans = traces[matched[0]]
        return jsonify([{
            "name": s.name,
            "span_id": format(s.context.span_id, "016x"),
            "parent_id": _parent_hex(s),
            "kind": s.kind.name,
            "start_unix_nano": str(s.start_time),
            "end_unix_nano": str(s.end_time),
            "duration_ms": (s.end_time - s.start_time) / 1e6,
            "attributes": dict(s.attributes),
            "status": s.status.status_code.name,
        } for s in spans])

    return app


def start_viewer(exporter, port=8899):
    if not HAS_FLASK:
        print("[viewer] 未安装 Flask，跳过查看服务。pip install flask", file=sys.stderr)
        return None
    app = build_viewer_app(exporter)
    t = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=port, debug=False, use_reloader=False
        ),
        daemon=True,
    )
    t.start()
    print(f"[viewer] http://127.0.0.1:{port}/")
    return t


# ============ 客户端 ============
class CalcClient:
    def __init__(self, tracer):
        self.tracer = tracer
        self.messages = []
        self.history = []

    def _do_request(self, text):
        """
        发送一次请求。结构（相对 click span 的子 span）：
          click
          ├── HTTP POST    ← requests 自动插桩（网络往返）
          ├── json.parse   ← r.json() + 提取 content
          └── ui.parse     ← 剥 Markdown + json.loads
        """
        self.messages.append({"role": "user", "content": text})

        # requests 自动插桩：自动创建 client span + 注入 traceparent
        r = requests.post(
            BASE,
            json={"model": MODEL, "messages": self.messages},
            timeout=30,
        )
        r.raise_for_status()

        # 子 span：JSON 解析
        with self.tracer.start_as_current_span("json.parse", kind=SpanKind.INTERNAL) as sp:
            sp.set_attribute("resp.size", len(r.text))
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            sp.set_attribute("content.length", len(content))

        # 子 span：kai-ui 解析
        with self.tracer.start_as_current_span("ui.parse", kind=SpanKind.INTERNAL) as sp:
            ui = parse_ui(content)
            sp.set_attribute("ui.type", ui.get("type", "?"))

        self.messages.append({"role": "assistant", "content": content})
        return ui

    def click(self, label, ui):
        state = get_display(ui) or "?"
        with self.tracer.start_as_current_span(label, kind=SpanKind.INTERNAL) as sp:
            sp.set_attribute("button.label", label)
            sp.set_attribute("calc.state", state)
            ev = find_event(ui, label)
            if not ev:
                sp.set_status(Status(StatusCode.ERROR, f"button not found: {label}"))
                raise RuntimeError(f"UI 里找不到按钮: {label}")
            sp.set_attribute("button.event", ev)
            return self._do_request(f"Pressed: {ev}")

    def notify(self, title, body):
        payload = {"cmd": "termux-notification", "args": ["-t", title, "-c", body]}
        text = f"/tool termux_api {json.dumps(payload, ensure_ascii=False)}"
        self.messages.append({"role": "user", "content": text})
        with self.tracer.start_as_current_span("notify", kind=SpanKind.INTERNAL) as sp:
            sp.set_attribute("notify.title", title)
            r = requests.post(
                BASE,
                json={"model": MODEL, "messages": self.messages},
                timeout=30,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            self.messages.append({"role": "assistant", "content": content})

    def calc(self, expression, notify=True):
        t0 = time.perf_counter()
        with self.tracer.start_as_current_span("calc", kind=SpanKind.INTERNAL) as root:
            root.set_attribute("calc.expr", expression)

            with self.tracer.start_as_current_span("start", kind=SpanKind.INTERNAL):
                ui = self._do_request("calc")

            ui = self.click("C", ui)
            for ch in expression:
                if ch.isspace():
                    continue
                if ch.isdigit():
                    ui = self.click(ch, ui)
                elif ch in OP_MAP:
                    ui = self.click(OP_MAP[ch], ui)
                else:
                    raise ValueError(f"不支持的字符: {ch!r}")
            ui = self.click("=", ui)
            result = get_display(ui)

            root.set_attribute("calc.result", result)

            ctx = root.get_span_context()
            trace_id = format(ctx.trace_id, "032x")
            total = time.perf_counter() - t0

            self.history.append({
                "trace_id": trace_id,
                "expr": expression,
                "result": result,
                "total": total,
            })

            print(f"  {expression} = {result}   [{total*1000:.0f} ms]   "
                  f"trace={trace_id[:8]}")
            print(f"  viewer: http://127.0.0.1:{VIEWER_PORT}/trace/{trace_id}")

            if notify:
                try:
                    self.notify("计算结果", f"{expression} = {result}")
                except Exception as e:
                    print(f"[通知失败] {e}", file=sys.stderr)
            return result, total

    def summary(self):
        if not self.history:
            print("还没有计算记录")
            return
        n = len(self.history)
        avg = sum(h["total"] for h in self.history) / n
        print(f"\n共 {n} 次计算，平均 {avg*1000:.0f} ms")

        traces = MEM.get_traces()
        by_kind = {}
        for spans in traces.values():
            for s in spans:
                by_kind.setdefault(s.kind.name, []).append(
                    (s.end_time - s.start_time) / 1e6
                )
        if by_kind:
            print("\n按 kind 分组：")
            print(f"  {'kind':<10}{'次数':>6}{'平均':>12}{'总耗时':>14}")
            print(f"  {'─'*10}{'─'*6}{'─'*12}{'─'*14}")
            for k in sorted(by_kind):
                ds = by_kind[k]
                print(f"  {k:<10}{len(ds):>6}{sum(ds)/len(ds):>10.1f}ms"
                      f"{sum(ds):>12.1f}ms")


# ============ 主程序 ============
VIEWER_PORT = 8899


def main():
    global VIEWER_PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--otlp", help="OTLP HTTP endpoint，如 http://localhost:4318/v1/traces")
    ap.add_argument("--port", type=int, default=8899, help="查看服务端口")
    ap.add_argument("--service", default="calc-client", help="service.name")
    ap.add_argument("expr", nargs="*", help="直接计算一个表达式后退出")
    args = ap.parse_args()
    VIEWER_PORT = args.port

    tracer = setup_otel(args.otlp, args.service)
    start_viewer(MEM, args.port)

    client = CalcClient(tracer)

    if args.expr:
        client.calc("".join(args.expr))
        client.summary()
        print("\n查看服务保持运行，Ctrl+C 退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    print("输入四则运算表达式，空行退出。")
    print("命令：:avg  :clear  :export [file]  :quit")
    print(f"[debug] stdin isatty={sys.stdin.isatty()}\n")

    while True:
        try:
            line = input("> ")
        except EOFError:
            print("\n[stdin EOF] 退出")
            break
        except KeyboardInterrupt:
            print()
            break
        if not line.strip():
            break
        line = line.strip()

        if line == ":avg":
            client.summary()
            continue

        if line == ":clear":
            MEM.clear()
            client.history.clear()
            print("已清空")
            continue

        if line.startswith(":export"):
            arg = line[len(":export"):].strip()
            if not arg:
                arg = f"traces-{int(time.time())}.json"
            try:
                n_traces, n_spans = export_otlp(MEM, arg)
                abspath = os.path.abspath(arg)
                print(f"已导出 {n_traces} 条 trace、{n_spans} 个 span")
                print(f"  文件: {abspath}")
                print(f"  大小: {os.path.getsize(arg)} 字节")
                print()
                print("导入方式：")
                print("  Tempo  : curl -X POST http://<tempo>:4318/v1/traces \\")
                print("             -H 'Content-Type: application/json' \\")
                print(f"             -d @{arg}")
                print("  Jaeger : 启用 OTLP receiver（端口 4318）后同上")
            except Exception as e:
                print(f"导出失败: {e}")
            continue

        if line == ":quit":
            break

        try:
            client.calc(line)
        except Exception as e:
            print(f"错误: {e}")


if __name__ == "__main__":
    main()