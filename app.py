#!/usr/bin/env python3
# app.py — 企业级 API 保护网关 + 浏览器指纹收集 + CALC 代币展示
import os
import time
import json
import sqlite3
import requests
from functools import wraps
from flask import (
    Flask, request, jsonify, Response,
)

# ============ 配置 ============
UPSTREAM = os.environ.get("CALC_API", "http://127.0.0.1:8900")
API_KEY = os.environ.get("CALC_API_TOKEN", "")
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", 8800))
FP_DB = os.environ.get("FP_DB", "fingerprints.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")


# ============ 指纹存储 ============
def _init_fp_db():
    conn = sqlite3.connect(FP_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fingerprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            fp_hash TEXT NOT NULL,
            user_agent TEXT,
            ip TEXT,
            data TEXT,
            UNIQUE(fp_hash)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS page_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            fp_hash TEXT,
            path TEXT,
            ip TEXT,
            referer TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_fingerprint(fp_hash, data, ip, ua):
    conn = sqlite3.connect(FP_DB)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO fingerprints (ts, fp_hash, user_agent, ip, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (time.time(), fp_hash, ua, ip, json.dumps(data, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def log_page_view(fp_hash, path, ip, referer):
    conn = sqlite3.connect(FP_DB)
    conn.execute(
        "INSERT INTO page_views (ts, fp_hash, path, ip, referer) VALUES (?, ?, ?, ?, ?)",
        (time.time(), fp_hash, path, ip, referer),
    )
    conn.commit()
    conn.close()


def query_fp_stats():
    conn = sqlite3.connect(FP_DB)
    conn.row_factory = sqlite3.Row
    total_fp = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
    total_pv = conn.execute("SELECT COUNT(*) FROM page_views").fetchone()[0]
    by_day = [dict(r) for r in conn.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS day, "
        "COUNT(*) AS n FROM page_views GROUP BY day ORDER BY day DESC LIMIT 14"
    ).fetchall()]
    recent_fp = [dict(r) for r in conn.execute(
        "SELECT ts, fp_hash, user_agent, ip FROM fingerprints "
        "ORDER BY ts DESC LIMIT 30"
    ).fetchall()]
    conn.close()
    return {
        "unique_fingerprints": total_fp,
        "page_views": total_pv,
        "by_day": by_day,
        "recent": recent_fp,
    }


_init_fp_db()


# ============ 上游调用 ============
def _api_headers(extra=None):
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    if extra:
        h.update(extra)
    return h


def call_upstream(expr, notify, extra_headers=None):
    headers = _api_headers({"X-Source": "web"})
    if extra_headers:
        headers.update(extra_headers)
    r = requests.post(
        f"{UPSTREAM}/api/calc",
        json={"expr": expr, "notify": notify},
        headers=headers,
        timeout=60,
    )
    return r.status_code, r.json()


def call_upstream_get(path, params=None):
    try:
        r = requests.get(
            f"{UPSTREAM}{path}",
            params=params or {},
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code >= 400:
            return None
        return r.json()
    except Exception:
        return None


def get_wallet(addr):
    return call_upstream_get(f"/api/wallet/{addr}")


def miner_addr_from_fp(fp):
    """和 calc_client 里的 miner 地址规则保持一致"""
    short = fp[:8] if fp else "default"
    return f"web:{short}"


# ============ 前端 HTML ============
INDEX_HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>企业级计算器 · Web 客户端</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
body{font-family:ui-monospace,Menlo,monospace;background:#0b0e14;color:#d8dee9;
     margin:0;padding:20px;min-height:100vh}
h1{color:#88c0d0;font-size:18px;margin:0 0 16px}
.card{background:#151a23;border:1px solid #2e3440;border-radius:8px;
      padding:16px;margin-bottom:16px}
.card h2{color:#81a1c1;font-size:13px;margin:0 0 12px;font-weight:600;
         display:flex;align-items:center;justify-content:space-between}
.card h2 .hint{color:#616e88;font-size:11px;font-weight:400}
.row{display:flex;gap:8px;align-items:center;margin-bottom:12px}
input{flex:1;background:#0b0e14;border:1px solid #2e3440;color:#d8dee9;
      padding:10px 12px;border-radius:6px;font-family:inherit;font-size:15px}
input:focus{outline:none;border-color:#88c0d0}
button{background:#88c0d0;color:#0b0e14;border:0;padding:10px 18px;
       border-radius:6px;font-weight:600;cursor:pointer;font-family:inherit}
button:hover{background:#a3d4e0}
button:disabled{opacity:.5;cursor:not-allowed}
.result{font-size:22px;color:#a3be8c;margin-top:12px;min-height:28px}
.meta{color:#616e88;font-size:12px;margin-top:8px}
.meta a{color:#88c0d0;text-decoration:none}
.meta a:hover{text-decoration:underline}
.fp{color:#616e88;font-size:11px;margin-top:20px;padding-top:12px;
    border-top:1px solid #2e3440}
.err{color:#bf616a}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}
.stat{background:#0b0e14;padding:10px;border-radius:6px;border:1px solid #2e3440}
.stat .n{color:#88c0d0;font-size:20px;font-weight:600}
.stat .l{color:#616e88;font-size:11px;margin-top:4px}
.stat.gold .n{color:#ebcb8b}

.calc-badges{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;
       font-weight:600}
.badge-cache{background:#a3be8c33;color:#a3be8c}
.badge-fresh{background:#d0877033;color:#d08770}
.badge-gold{background:#ebcb8b22;color:#ebcb8b}
.badge-chain{background:#88c0d022;color:#88c0d0}

.wallet-box{background:#0b0e14;border:1px solid #ebcb8b55;border-radius:6px;
            padding:12px;margin-bottom:12px}
.wallet-box .addr{color:#b48ead;font-size:11px;font-family:monospace;
                  word-break:break-all}
.wallet-box .bal{color:#ebcb8b;font-size:24px;font-weight:700;margin-top:6px}
.wallet-box .sub{color:#616e88;font-size:11px;margin-top:4px}

table{border-collapse:collapse;width:100%;font-size:12px}
th,td{padding:6px 10px;border-bottom:1px solid #2e3440;text-align:left}
th{color:#81a1c1;font-weight:600}
tr:hover{background:#0b0e14}
.miner-addr{font-family:monospace;font-size:10px;color:#b48ead}
.gold{color:#ebcb8b;font-weight:600}
</style>
</head>
<body>
<h1>企业级计算器 · Web 客户端</h1>

<div class="card">
  <h2>⛓ CALC 链 <span class="hint" id="chain-hint">加载中…</span></h2>

  <div class="wallet-box" id="my-wallet">
    <div class="addr" id="wallet-addr">正在获取钱包…</div>
    <div class="bal" id="wallet-bal">— CALC</div>
    <div class="sub" id="wallet-sub">— 块 · — 次 PoW 尝试</div>
  </div>

  <div class="stats" id="chain-stats"></div>

  <h2 style="margin-top:16px">矿工榜</h2>
  <table id="miners-table">
    <thead><tr><th>地址</th><th>余额</th><th>块数</th><th>PoW 尝试</th></tr></thead>
    <tbody><tr><td colspan="4" style="color:#616e88">加载中…</td></tr></tbody>
  </table>
</div>

<div class="card">
  <h2>计算器</h2>
  <div class="row">
    <input id="expr" placeholder="输入表达式，如 12+34*5" autofocus>
    <button id="go">计算</button>
  </div>
  <label style="color:#616e88;font-size:12px">
    <input type="checkbox" id="notify"> 发送 Termux 通知
  </label>
  <div class="result" id="result"></div>
  <div class="calc-badges" id="badges"></div>
  <div class="meta" id="meta"></div>
</div>

<div class="card">
  <h2>服务端统计</h2>
  <div class="stats" id="stats"></div>
</div>

<div class="fp" id="fp-info">正在采集浏览器指纹…</div>

<script>
// ============ 通用 ============
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function fmtUsd(v) {
  if (!v || v === 0) return '$0';
  if (v < 0.0001) return '$' + v.toExponential(2);
  return '$' + v.toFixed(8);
}

function fmtCalc(v) {
  if (v == null) return '0';
  return v.toFixed(4);
}

// ============ 浏览器指纹 ============
async function collectFingerprint() {
  const parts = [];
  parts.push(navigator.userAgent);
  parts.push(navigator.language);
  parts.push(navigator.languages ? navigator.languages.join(',') : '');
  parts.push(navigator.platform || '');
  parts.push(navigator.hardwareConcurrency || '');
  parts.push(navigator.deviceMemory || '');
  parts.push(screen.width + 'x' + screen.height + 'x' + screen.colorDepth);
  parts.push(new Date().getTimezoneOffset());
  parts.push(Intl.DateTimeFormat().resolvedOptions().timeZone || '');
  parts.push(navigator.maxTouchPoints || 0);

  try {
    const c = document.createElement('canvas');
    c.width = 200; c.height = 50;
    const ctx = c.getContext('2d');
    ctx.textBaseline = 'top';
    ctx.font = '14px Arial';
    ctx.fillStyle = '#f60';
    ctx.fillRect(0, 0, 100, 30);
    ctx.fillStyle = '#069';
    ctx.fillText('fingerprint-me', 2, 15);
    ctx.fillStyle = 'rgba(102,204,0,0.7)';
    ctx.fillText('fingerprint-me', 4, 17);
    parts.push(c.toDataURL().slice(-64));
  } catch(e) {}

  try {
    const gl = document.createElement('canvas').getContext('webgl');
    if (gl) {
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      if (dbg) {
        parts.push(gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL));
        parts.push(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL));
      }
    }
  } catch(e) {}

  const raw = parts.join('|');
  const buf = new TextEncoder().encode(raw);
  const hash = await crypto.subtle.digest('SHA-256', buf);
  const fpHash = Array.from(new Uint8Array(hash))
    .map(b => b.toString(16).padStart(2, '0')).join('');

  return {
    hash: fpHash,
    data: {
      ua: navigator.userAgent,
      lang: navigator.language,
      platform: navigator.platform,
      screen: screen.width + 'x' + screen.height,
      tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
      cores: navigator.hardwareConcurrency || null,
    }
  };
}

let FP_HASH = '';

async function register() {
  const fp = await collectFingerprint();
  FP_HASH = fp.hash;
  document.getElementById('fp-info').textContent =
    '指纹 ID: ' + fp.hash.slice(0, 16) + '…';

  try {
    const r = await fetch('/api/fp', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(fp),
    });
    const d = await r.json();
    if (d.wallet) renderMyWallet(d.wallet);
  } catch(e) {}
}

// ============ 钱包渲染 ============
function renderMyWallet(w) {
  const addr = w.address || '—';
  const bal = w.balance || 0;
  const blocks = w.blocks_mined || 0;
  const attempts = w.total_attempts || 0;

  document.getElementById('wallet-addr').textContent = addr;
  document.getElementById('wallet-bal').textContent = fmtCalc(bal) + ' CALC';
  document.getElementById('wallet-sub').textContent =
    `${blocks} 块 · ${attempts.toLocaleString()} 次 PoW 尝试`;
}

function renderChainStats(s) {
  const el = document.getElementById('chain-stats');
  el.innerHTML = `
    <div class="stat gold"><div class="n">${s.height}</div><div class="l">链高度</div></div>
    <div class="stat"><div class="n">${s.next_difficulty}</div><div class="l">PoW 难度</div></div>
    <div class="stat"><div class="n">${(s.total_work||0).toLocaleString()}</div><div class="l">总工作量</div></div>
    <div class="stat gold"><div class="n">${fmtCalc(s.total_reward_calc)}</div><div class="l">CALC 供应</div></div>
    <div class="stat"><div class="n">${s.wallet_count}</div><div class="l">钱包数</div></div>
    <div class="stat gold"><div class="n">${fmtUsd(s.market_cap_usd)}</div><div class="l">市值 (USD)</div></div>
  `;
  document.getElementById('chain-hint').textContent =
    `1 CALC = ${fmtUsd(s.calc_price_usd)} · 缓存块 ${s.cached_blocks||0}`;
}

function renderMiners(wallets) {
  const tb = document.querySelector('#miners-table tbody');
  if (!wallets || !wallets.length) {
    tb.innerHTML = '<tr><td colspan="4" style="color:#616e88">暂无矿工</td></tr>';
    return;
  }
  tb.innerHTML = wallets.slice(0, 10).map(w => `
    <tr>
      <td class="miner-addr">${escapeHtml(w.address)}</td>
      <td class="gold">${fmtCalc(w.balance)} CALC</td>
      <td>${w.blocks_mined}</td>
      <td>${(w.total_attempts||0).toLocaleString()}</td>
    </tr>
  `).join('');
}

async function loadChain() {
  try {
    const [stats, wallets] = await Promise.all([
      fetch('/api/chain_stats').then(r => r.json()),
      fetch('/api/wallets').then(r => r.json()),
    ]);
    if (stats && !stats.error) renderChainStats(stats);
    if (Array.isArray(wallets)) renderMiners(wallets);
  } catch(e) {}
}

// ============ 计算 ============
async function doCalc() {
  const expr = document.getElementById('expr').value.trim();
  if (!expr) return;
  const notify = document.getElementById('notify').checked;
  const btn = document.getElementById('go');
  const resEl = document.getElementById('result');
  const metaEl = document.getElementById('meta');
  const badgesEl = document.getElementById('badges');

  btn.disabled = true;
  resEl.textContent = '计算中…（含 PoW 挖矿）';
  resEl.className = 'result';
  metaEl.textContent = '';
  badgesEl.innerHTML = '';

  try {
    const r = await fetch('/api/calc', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expr, notify, fingerprint: FP_HASH}),
    });
    const data = await r.json();
    if (data.ok) {
      resEl.textContent = data.expr + ' = ' + data.result;

      // badges
      const badges = [];
      if (data.cached) {
        badges.push('<span class="badge badge-cache">缓存命中</span>');
      } else {
        badges.push('<span class="badge badge-fresh">新计算</span>');
      }
      if (data.chain && data.chain.block_idx >= 0) {
        badges.push(`<span class="badge badge-chain">块 #${data.chain.block_idx}</span>`);
        if (data.chain.reward != null) {
          badges.push(`<span class="badge badge-gold">+${fmtCalc(data.chain.reward)} CALC</span>`);
        }
        if (data.chain.difficulty != null) {
          badges.push(`<span class="badge badge-chain">难度 ${data.chain.difficulty} · PoW ${(data.chain.pow_attempts||0).toLocaleString()} 次 / ${data.chain.pow_elapsed_ms}ms</span>`);
        }
      }
      badgesEl.innerHTML = badges.join('');

      const traceLink = `<a href="http://${location.hostname}:8899/trace/${data.trace_id}" target="_blank">${data.trace_id.slice(0,8)}</a>`;
      metaEl.innerHTML = `${data.duration_ms} ms · trace ${traceLink}`;

      // 计算完刷新链数据 + 我的钱包
      setTimeout(loadChain, 300);
      setTimeout(refreshWallet, 500);
    } else {
      resEl.textContent = '错误：' + data.error;
      resEl.className = 'result err';
    }
  } catch (e) {
    resEl.textContent = '请求失败：' + e;
    resEl.className = 'result err';
  } finally {
    btn.disabled = false;
    loadStats();
  }
}

async function refreshWallet() {
  if (!FP_HASH) return;
  try {
    const r = await fetch('/api/my_wallet?fp=' + encodeURIComponent(FP_HASH));
    const d = await r.json();
    if (d && !d.error) renderMyWallet(d);
  } catch(e) {}
}

// ============ 服务端统计 ============
async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    const s = await r.json();
    const el = document.getElementById('stats');
    el.innerHTML =
      `<div class="stat"><div class="n">${s.total || 0}</div><div class="l">总计算</div></div>
       <div class="stat"><div class="n">${s.avg_ms ? s.avg_ms.toFixed(0) : 0}ms</div><div class="l">平均耗时</div></div>
       <div class="stat"><div class="n">${s.unique_fingerprints || 0}</div><div class="l">独立指纹</div></div>
       <div class="stat"><div class="n">${s.page_views || 0}</div><div class="l">页面访问</div></div>`;
  } catch(e) {}
}

document.getElementById('go').onclick = doCalc;
document.getElementById('expr').addEventListener('keydown', e => {
  if (e.key === 'Enter') doCalc();
});

// 启动
register();
loadChain();
loadStats();
setInterval(loadChain, 10000);
setInterval(loadStats, 10000);
</script>
</body>
</html>
"""


# ============ 路由 ============
@app.route("/")
def index():
    fp = request.cookies.get("fp", "")
    log_page_view(fp, "/", request.remote_addr or "",
                  request.headers.get("Referer", ""))
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/api/fp", methods=["POST"])
def api_fp():
    data = request.get_json(silent=True) or {}
    fp_hash = data.get("hash", "")
    if not fp_hash:
        return jsonify({"ok": False}), 400

    save_fingerprint(fp_hash, data.get("data", {}),
                     request.remote_addr or "",
                     request.headers.get("User-Agent", ""))

    # 顺便查一下这个指纹对应的钱包
    addr = miner_addr_from_fp(fp_hash)
    wallet = get_wallet(addr) or {"address": addr, "balance": 0,
                                   "blocks_mined": 0, "total_attempts": 0}

    resp = jsonify({"ok": True, "wallet": wallet})
    resp.set_cookie("fp", fp_hash, max_age=365*24*3600, samesite="Lax")
    return resp


@app.route("/api/my_wallet")
def api_my_wallet():
    fp = request.args.get("fp", "") or request.cookies.get("fp", "")
    if not fp:
        return jsonify({"error": "missing fp"}), 400
    addr = miner_addr_from_fp(fp)
    wallet = get_wallet(addr) or {"address": addr, "balance": 0,
                                   "blocks_mined": 0, "total_attempts": 0}
    return jsonify(wallet)


@app.route("/api/calc", methods=["POST"])
def api_calc():
    data = request.get_json(silent=True) or {}
    expr = (data.get("expr") or "").strip()
    if not expr:
        return jsonify({"ok": False, "error": "missing expr"}), 400

    notify = bool(data.get("notify", False))
    fingerprint = data.get("fingerprint", "")

    try:
        status, payload = call_upstream(expr, notify, extra_headers={
            "X-Fingerprint": fingerprint[:64],
            "X-Forwarded-For": request.remote_addr or "",
        })
        return jsonify(payload), status
    except requests.exceptions.RequestException as e:
        return jsonify({"ok": False, "error": f"upstream: {e}"}), 502


@app.route("/api/chain_stats")
def api_chain_stats():
    data = call_upstream_get("/api/chain/stats")
    if data is None:
        return jsonify({"error": "upstream unavailable"}), 502
    return jsonify(data)


@app.route("/api/wallets")
def api_wallets():
    limit = min(int(request.args.get("limit", 10)), 100)
    data = call_upstream_get("/api/wallets", {"limit": limit})
    if data is None:
        return jsonify([])
    return jsonify(data)


@app.route("/api/chain")
def api_chain():
    limit = min(int(request.args.get("limit", 10)), 100)
    data = call_upstream_get("/api/chain", {"limit": limit})
    if data is None:
        return jsonify([])
    return jsonify(data)


@app.route("/api/stats")
def api_stats():
    out = {"total": 0, "avg_ms": 0, "unique_fingerprints": 0, "page_views": 0}

    try:
        s = call_upstream_get("/api/stats")
        if s:
            out["total"] = s.get("total", 0)
            out["avg_ms"] = s.get("avg_ms", 0)
    except Exception:
        pass

    fp = query_fp_stats()
    out["unique_fingerprints"] = fp["unique_fingerprints"]
    out["page_views"] = fp["page_views"]
    return jsonify(out)


@app.route("/health")
def health():
    return jsonify({"ok": True, "upstream": UPSTREAM, "auth": bool(API_KEY)})


if __name__ == "__main__":
    print(f"[frontend] http://127.0.0.1:{FRONTEND_PORT}/")
    print(f"[frontend] upstream: {UPSTREAM} (auth={'on' if API_KEY else 'off'})")
    print(f"[frontend] fp db: {os.path.abspath(FP_DB)}")
    app.run(host="0.0.0.0", port=FRONTEND_PORT, debug=False, threaded=True)