#!/usr/bin/env python3
# sdk.py — 密码登录的数据看板 SDK（含 SVG 图表 + 缓存统计 + 区块链看板）
import os
import time
import json
import sqlite3
import hashlib
import logging
import requests
from functools import wraps
from flask import (
    Flask, request, jsonify, render_template_string,
    session, redirect, url_for
)

logging.getLogger("werkzeug").setLevel(logging.ERROR)

# ============ 配置 ============
CALC_API = os.environ.get("CALC_API", "http://127.0.0.1:8900")
CALC_API_TOKEN = os.environ.get("CALC_API_TOKEN", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
SDK_PORT = int(os.environ.get("SDK_PORT", 8700))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sdk-secret-change-me")


# ============ 密码校验 ============
def _hash_pw(password, salt=None):
    if salt is None:
        salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + ":" + h.hex()


def _verify_pw(password, stored):
    try:
        salt_hex, _ = stored.split(":", 1)
        return _hash_pw(password, bytes.fromhex(salt_hex)) == stored
    except Exception:
        return False


PW_FILE = os.environ.get("PW_FILE", "admin.pw")
if not os.path.exists(PW_FILE):
    with open(PW_FILE, "w") as f:
        f.write(_hash_pw(ADMIN_PASSWORD))
    print(f"[sdk] 初始密码已写入 {PW_FILE}（默认: {ADMIN_PASSWORD}）")


def check_password(password):
    with open(PW_FILE) as f:
        stored = f.read().strip()
    return _verify_pw(password, stored)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ============ 调用 calc_client ============
def api_headers():
    h = {"Content-Type": "application/json"}
    if CALC_API_TOKEN:
        h["Authorization"] = f"Bearer {CALC_API_TOKEN}"
    return h


def fetch_stats():
    r = requests.get(f"{CALC_API}/api/stats", headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_history(limit=100):
    r = requests.get(f"{CALC_API}/api/history",
                     params={"limit": limit},
                     headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_by_fingerprint(limit_per_fp=20):
    r = requests.get(f"{CALC_API}/api/by_fingerprint",
                     params={"limit": limit_per_fp},
                     headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_cache():
    r = requests.get(f"{CALC_API}/api/cache",
                     headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def clear_cache():
    r = requests.post(f"{CALC_API}/api/cache/clear",
                      headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def run_calc(expr, notify=False):
    r = requests.post(f"{CALC_API}/api/calc",
                      json={"expr": expr, "notify": notify},
                      headers=api_headers(), timeout=30)
    return r.status_code, r.json()


# ---- 区块链 ----
def fetch_chain_stats():
    r = requests.get(f"{CALC_API}/api/chain/stats",
                     headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_chain(limit=30):
    r = requests.get(f"{CALC_API}/api/chain",
                     params={"limit": limit},
                     headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_wallets(limit=20):
    r = requests.get(f"{CALC_API}/api/wallets",
                     params={"limit": limit},
                     headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_transactions(limit=20):
    r = requests.get(f"{CALC_API}/api/transactions",
                     params={"limit": limit},
                     headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def verify_chain():
    r = requests.get(f"{CALC_API}/api/chain/verify",
                     headers=api_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


# ============ 登录页 ============
LOGIN_HTML = r"""
<!doctype html><meta charset="utf-8">
<title>SDK 登录</title>
<style>
body{font-family:ui-monospace,Menlo,monospace;background:#0b0e14;color:#d8dee9;
     display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#151a23;border:1px solid #2e3440;border-radius:8px;padding:32px;
     width:320px}
h1{color:#88c0d0;font-size:16px;margin:0 0 20px;text-align:center}
input{width:100%;background:#0b0e14;border:1px solid #2e3440;color:#d8dee9;
      padding:10px 12px;border-radius:6px;font-family:inherit;margin-bottom:12px;
      box-sizing:border-box}
input:focus{outline:none;border-color:#88c0d0}
button{width:100%;background:#88c0d0;color:#0b0e14;border:0;padding:10px;
       border-radius:6px;font-weight:600;cursor:pointer}
button:hover{background:#a3d4e0}
.err{color:#bf616a;font-size:12px;text-align:center;margin-top:8px;min-height:16px}
</style>
<form method="post" class="box">
  <h1>SDK 数据看板</h1>
  <input name="password" type="password" placeholder="密码" autofocus autocomplete="current-password">
  <button type="submit">登录</button>
  <div class="err">{{ error }}</div>
</form>
"""


DASH_HTML = r"""
<!doctype html><meta charset="utf-8">
<title>SDK 数据看板</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
body{font-family:ui-monospace,Menlo,monospace;background:#0b0e14;color:#d8dee9;
     margin:0;padding:20px}
h1{color:#88c0d0;font-size:18px;margin:0 0 4px}
h3{color:#81a1c1;font-size:12px;margin:0 0 8px}
.sub{color:#616e88;font-size:12px;margin-bottom:20px}
.sub a{color:#88c0d0;text-decoration:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
      gap:10px;margin-bottom:20px}
.stat{background:#151a23;border:1px solid #2e3440;border-radius:8px;padding:14px}
.stat .n{color:#88c0d0;font-size:22px;font-weight:600;line-height:1}
.stat .l{color:#616e88;font-size:11px;margin-top:6px}
.stat.highlight .n{color:#a3be8c}
.stat.gold .n{color:#ebcb8b}
.card{background:#151a23;border:1px solid #2e3440;border-radius:8px;
      padding:16px;margin-bottom:16px}
.card h2{color:#81a1c1;font-size:13px;margin:0 0 12px;font-weight:600;
         display:flex;align-items:center;justify-content:space-between}
.card h2 .hint{color:#616e88;font-size:11px;font-weight:400}
.charts-2col{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:900px){.charts-2col{grid-template-columns:1fr}}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{padding:6px 10px;border-bottom:1px solid #2e3440;text-align:left}
th{color:#81a1c1;font-weight:600}
tr:hover{background:#0b0e14}
.bar{display:inline-block;height:12px;background:#88c0d0;border-radius:2px;
     vertical-align:middle;margin-right:6px}
input,button{background:#0b0e14;border:1px solid #2e3440;color:#d8dee9;
             padding:8px 12px;border-radius:6px;font-family:inherit}
button{background:#88c0d0;color:#0b0e14;font-weight:600;cursor:pointer;border:0}
button:hover{background:#a3d4e0}
.btn-sm{padding:4px 10px;font-size:11px}
.btn-gold{background:#ebcb8b;color:#0b0e14}
.btn-gold:hover{background:#f0d79a}
pre{background:#0b0e14;padding:12px;border-radius:6px;overflow:auto;
    font-size:11px;max-height:400px;margin:0}
svg{display:block;max-width:100%}
.chart-empty{color:#616e88;font-size:12px;padding:20px;text-align:center}

.cache-badge{display:inline-block;padding:1px 6px;border-radius:8px;
             font-size:10px;font-weight:600;margin-left:4px}
.cache-hit{background:#a3be8c33;color:#a3be8c}
.cache-miss{background:#d0877033;color:#d08770}

.chain-hash{font-family:monospace;font-size:10px;color:#88c0d0}
.chain-arrow{color:#616e88;margin:0 4px}
.chain-block{display:inline-block;padding:6px 10px;background:#0b0e14;
             border:1px solid #2e3440;border-radius:6px;margin:4px 0;
             font-size:11px;max-width:200px;overflow:hidden;
             text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}
.chain-block.genesis{border-color:#ebcb8b55}
.chain-block.tip{border-color:#a3be8c}
.miner-addr{font-family:monospace;font-size:10px;color:#b48ead}
.pow-badge{display:inline-block;padding:1px 6px;border-radius:8px;
           font-size:10px;background:#ebcb8b22;color:#ebcb8b}
.chain-status-ok{color:#a3be8c}
.chain-status-bad{color:#bf616a}

details.fp{background:#0b0e14;border:1px solid #2e3440;border-radius:6px;
           margin-bottom:8px;overflow:hidden}
details.fp[open]{border-color:#88c0d0}
details.fp > summary{cursor:pointer;padding:10px 12px;font-size:13px;
                     list-style:none;display:flex;align-items:center;gap:10px;
                     user-select:none}
details.fp > summary::-webkit-details-marker{display:none}
details.fp > summary::before{content:"▶";color:#88c0d0;font-size:10px;
                              transition:transform .15s;display:inline-block}
details.fp[open] > summary::before{transform:rotate(90deg)}
details.fp > summary:hover{background:#151a23}
.fp-hash{color:#88c0d0;font-weight:600;font-family:monospace}
.fp-meta{color:#616e88;font-size:11px;margin-left:auto;display:flex;gap:12px}
.fp-meta b{color:#a3be8c;font-weight:600}
.fp-body{padding:0 12px 12px}
.fp-src{color:#616e88;font-size:11px;margin-bottom:8px}
.fp-src span{display:inline-block;background:#151a23;padding:2px 8px;
             border-radius:10px;margin-right:6px;color:#88c0d0}

.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:10px;font-size:11px}
.legend-item{display:flex;align-items:center;gap:5px}
.legend-dot{width:10px;height:10px;border-radius:2px;display:inline-block}

.chain-scroll{overflow-x:auto;white-space:nowrap;padding:4px 0}
</style>
<h1>SDK 数据看板</h1>
<div class="sub">
  登录为 <b>{{ user }}</b> ·
  <a href="/refresh">刷新</a> ·
  <a href="#" onclick="clearCache(); return false;">清空缓存</a> ·
  <a href="#" onclick="verifyChain(); return false;">校验链</a> ·
  <a href="/logout">退出</a> ·
  上游 {{ upstream }}
</div>

<div class="grid" id="stats-grid">加载中…</div>

<!-- ==================== 区块链看板 ==================== -->
<div class="card" id="chain-card">
  <h2>
    <span>⛓ CALC 链</span>
    <span id="chain-verify-badge"></span>
  </h2>
  <div class="grid" id="chain-stats">加载中…</div>
  <h3>最新区块（点击看详情）</h3>
  <div class="chain-scroll" id="chain-tip">加载中…</div>
  <div class="charts-2col" style="margin-top:16px">
    <div>
      <h3>区块列表</h3>
      <table id="chain-table"></table>
    </div>
    <div>
      <h3>钱包排行</h3>
      <table id="wallets-table"></table>
    </div>
  </div>
  <h3 style="margin-top:16px">最近交易</h3>
  <table id="tx-table"></table>
</div>

<div class="charts-2col">
  <div class="card">
    <h2>缓存命中率 <span class="hint">数据库累计 + 内存实时</span></h2>
    <div id="chart-cache">加载中…</div>
  </div>
  <div class="card">
    <h2>来源分布 <span class="hint">按 source</span></h2>
    <div id="chart-source">加载中…</div>
  </div>
</div>

<div class="card">
  <h2>每小时计算量 / 缓存命中 <span class="hint">最近 24 小时</span></h2>
  <div id="chart-hourly">加载中…</div>
  <div class="legend">
    <div class="legend-item">
      <span class="legend-dot" style="background:#88c0d0"></span>
      <span>总请求</span>
    </div>
    <div class="legend-item">
      <span class="legend-dot" style="background:#a3be8c"></span>
      <span>缓存命中</span>
    </div>
  </div>
</div>

<div class="charts-2col">
  <div class="card">
    <h2>Top 表达式 <span class="hint">含缓存命中数</span></h2>
    <div id="chart-top">加载中…</div>
  </div>
  <div class="card">
    <h2>耗时分布 <span class="hint">未命中缓存的真实耗时</span></h2>
    <div id="chart-duration">加载中…</div>
  </div>
</div>

<div class="card">
  <h2>快速计算（走上游 API）</h2>
  <div style="display:flex;gap:8px">
    <input id="expr" placeholder="如 12+34*5" style="flex:1">
    <button onclick="quickCalc()">计算</button>
  </div>
  <div id="calc-result" style="margin-top:10px;color:#a3be8c;font-size:14px"></div>
</div>

<div class="card">
  <h2>
    <span>按用户指纹分组</span>
    <span>
      <button class="btn-sm" onclick="expandAllFp()">展开全部</button>
      <button class="btn-sm" onclick="collapseAllFp()">折叠全部</button>
    </span>
  </h2>
  <div id="by-fp">加载中…</div>
</div>

<div class="card">
  <h2>最近计算记录 <span class="hint">绿标 = 缓存命中，链标 = 已上链</span></h2>
  <table id="history-table">
    <thead><tr>
      <th>时间</th><th>表达式</th><th>结果</th><th>耗时</th>
      <th>缓存</th><th>链</th><th>来源</th><th>Trace</th>
    </tr></thead>
    <tbody><tr><td colspan="8">加载中…</td></tr></tbody>
  </table>
</div>

<script>
const PALETTE = ['#88c0d0', '#a3be8c', '#d08770', '#b48ead', '#ebcb8b',
                 '#5e81ac', '#bf616a', '#8fbcbb', '#d8dee9'];

function fmtTs(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleString('zh-CN', {hour12: false});
}

function fmtTsShort(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN', {hour12: false});
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function svgEl(tag, attrs, text) {
  const a = Object.entries(attrs)
    .map(([k,v]) => `${k}="${escapeHtml(String(v))}"`).join(' ');
  return text != null ? `<${tag} ${a}>${escapeHtml(String(text))}</${tag}>`
                      : `<${tag} ${a}/>`;
}

// ============ 柱状图 ============
function barChart(data, opts = {}) {
  if (!data || !data.length) return '<div class="chart-empty">暂无数据</div>';
  const W = 700, H = opts.height || 220;
  const padL = 40, padR = 10, padT = 10, padB = 50;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const maxV = Math.max(1, ...data.map(d => d.value));
  const step = plotW / data.length;
  const barW = Math.max(2, step * 0.7);
  const parts = [`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">`];

  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH * i / 4);
    const v = Math.round(maxV * (4 - i) / 4);
    parts.push(svgEl('line', {x1: padL, y1: y, x2: W - padR, y2: y,
                              stroke: '#2e3440', 'stroke-width': 1}));
    parts.push(svgEl('text', {x: padL - 6, y: y + 4, fill: '#616e88',
                              'font-size': 10, 'text-anchor': 'end'}, v));
  }

  data.forEach((d, i) => {
    const x = padL + i * step + (step - barW) / 2;
    const h = (d.value / maxV) * plotH;
    const y = padT + plotH - h;
    const color = opts.color || PALETTE[0];
    parts.push(`<g><title>${escapeHtml(d.label)}: ${d.value}</title>`);
    parts.push(svgEl('rect', {
      x: x.toFixed(1), y: y.toFixed(1),
      width: barW.toFixed(1), height: Math.max(h, 1).toFixed(1),
      fill: color, rx: 2,
    }));
    parts.push('</g>');
    const every = Math.ceil(data.length / 8);
    if (i % every === 0) {
      const lx = padL + i * step + step / 2;
      parts.push(svgEl('text', {
        x: lx.toFixed(1), y: H - padB + 14,
        fill: '#616e88', 'font-size': 9, 'text-anchor': 'end',
        transform: `rotate(-35 ${lx.toFixed(1)} ${H - padB + 14})`,
      }, d.label));
    }
  });

  parts.push('</svg>');
  return parts.join('');
}

// ============ 双序列柱状图 ============
function dualBarChart(data, opts = {}) {
  if (!data || !data.length) return '<div class="chart-empty">暂无数据</div>';
  const W = 700, H = opts.height || 240;
  const padL = 40, padR = 10, padT = 10, padB = 50;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const maxV = Math.max(1, ...data.map(d => d.value));
  const step = plotW / data.length;
  const slotW = step * 0.7;
  const barW = slotW / 2 - 1;
  const parts = [`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">`];

  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH * i / 4);
    const v = Math.round(maxV * (4 - i) / 4);
    parts.push(svgEl('line', {x1: padL, y1: y, x2: W - padR, y2: y,
                              stroke: '#2e3440', 'stroke-width': 1}));
    parts.push(svgEl('text', {x: padL - 6, y: y + 4, fill: '#616e88',
                              'font-size': 10, 'text-anchor': 'end'}, v));
  }

  data.forEach((d, i) => {
    const slotX = padL + i * step + (step - slotW) / 2;

    const h1 = (d.value / maxV) * plotH;
    const y1 = padT + plotH - h1;
    parts.push(`<g><title>${escapeHtml(d.label)} 总: ${d.value}</title>`);
    parts.push(svgEl('rect', {
      x: slotX.toFixed(1), y: y1.toFixed(1),
      width: barW.toFixed(1), height: Math.max(h1, 1).toFixed(1),
      fill: '#88c0d0', rx: 2,
    }));
    parts.push('</g>');

    const subV = d.sub || 0;
    if (subV > 0) {
      const h2 = (subV / maxV) * plotH;
      const y2 = padT + plotH - h2;
      parts.push(`<g><title>${escapeHtml(d.label)} 命中: ${subV}</title>`);
      parts.push(svgEl('rect', {
        x: (slotX + barW + 2).toFixed(1), y: y2.toFixed(1),
        width: barW.toFixed(1), height: Math.max(h2, 1).toFixed(1),
        fill: '#a3be8c', rx: 2,
      }));
      parts.push('</g>');
    }

    const every = Math.ceil(data.length / 8);
    if (i % every === 0) {
      const lx = padL + i * step + step / 2;
      parts.push(svgEl('text', {
        x: lx.toFixed(1), y: H - padB + 14,
        fill: '#616e88', 'font-size': 9, 'text-anchor': 'end',
        transform: `rotate(-35 ${lx.toFixed(1)} ${H - padB + 14})`,
      }, d.label));
    }
  });

  parts.push('</svg>');
  return parts.join('');
}

// ============ 环形图 ============
function donutChart(data, opts = {}) {
  if (!data || !data.length) return '<div class="chart-empty">暂无数据</div>';
  const size = opts.size || 200;
  const cx = size / 2, cy = size / 2;
  const r = size * 0.38;
  const inner = r * 0.6;
  const total = data.reduce((s, d) => s + d.value, 0) || 1;
  const parts = [`<svg viewBox="0 0 ${size} ${size}" style="max-width:${size}px">`];
  let angle = -Math.PI / 2;

  data.forEach((d, i) => {
    const frac = d.value / total;
    const a2 = angle + frac * Math.PI * 2;
    const x1 = cx + r * Math.cos(angle);
    const y1 = cy + r * Math.sin(angle);
    const x2 = cx + r * Math.cos(a2);
    const y2 = cy + r * Math.sin(a2);
    const xi2 = cx + inner * Math.cos(a2);
    const yi2 = cy + inner * Math.sin(a2);
    const xi1 = cx + inner * Math.cos(angle);
    const yi1 = cy + inner * Math.sin(angle);
    const large = frac > 0.5 ? 1 : 0;
    const color = PALETTE[i % PALETTE.length];
    const pct = (frac * 100).toFixed(1);

    const path = `M ${x1.toFixed(2)} ${y1.toFixed(2)} ` +
      `A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} ` +
      `L ${xi2.toFixed(2)} ${yi2.toFixed(2)} ` +
      `A ${inner} ${inner} 0 ${large} 0 ${xi1.toFixed(2)} ${yi1.toFixed(2)} Z`;

    parts.push(`<g><title>${escapeHtml(d.label)}: ${d.value} (${pct}%)</title>`);
    parts.push(svgEl('path', {d: path, fill: color}));
    parts.push('</g>');
    angle = a2;
  });

  const centerText = opts.center != null ? opts.center : total;
  parts.push(svgEl('text', {x: cx, y: cy - 2, fill: '#88c0d0',
                            'font-size': 22, 'font-weight': 700,
                            'text-anchor': 'middle'}, centerText));
  parts.push(svgEl('text', {x: cx, y: cy + 16, fill: '#616e88',
                            'font-size': 10, 'text-anchor': 'middle'},
                   opts.centerLabel || '总计'));
  parts.push('</svg>');

  const legend = data.map((d, i) => {
    const color = PALETTE[i % PALETTE.length];
    const pct = ((d.value / total) * 100).toFixed(1);
    return `<div class="legend-item">
      <span class="legend-dot" style="background:${color}"></span>
      <span>${escapeHtml(d.label)}</span>
      <span style="color:#616e88">${d.value} · ${pct}%</span>
    </div>`;
  }).join('');

  return parts.join('') + `<div class="legend">${legend}</div>`;
}

// ============ 水平条形图 ============
function hBarChart(data, opts = {}) {
  if (!data || !data.length) return '<div class="chart-empty">暂无数据</div>';
  const rowH = 26;
  const H = data.length * rowH + 10;
  const labelW = opts.labelW || 160;
  const W = 700;
  const barMaxW = W - labelW - 80;
  const maxV = Math.max(1, ...data.map(d => d.value));
  const parts = [`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">`];

  data.forEach((d, i) => {
    const y = i * rowH + 5;
    const w = (d.value / maxV) * barMaxW;
    const color = PALETTE[i % PALETTE.length];

    parts.push(svgEl('text', {
      x: labelW - 8, y: y + 16, fill: '#d8dee9',
      'font-size': 12, 'text-anchor': 'end',
    }, d.label.length > 18 ? d.label.slice(0, 17) + '…' : d.label));

    parts.push(`<g><title>${escapeHtml(d.label)}: 总 ${d.value}${d.sub ? `, 命中 ${d.sub}` : ''}</title>`);
    parts.push(svgEl('rect', {
      x: labelW, y: y + 4,
      width: Math.max(w, 1).toFixed(1), height: 18,
      fill: color, rx: 3,
    }));
    parts.push('</g>');

    if (d.sub) {
      const sw = (d.sub / maxV) * barMaxW;
      parts.push(svgEl('rect', {
        x: labelW, y: y + 4,
        width: Math.max(sw, 1).toFixed(1), height: 18,
        fill: '#a3be8c', rx: 3, opacity: 0.85,
      }));
    }

    const label = d.sub ? `${d.value} (命中 ${d.sub})` : `${d.value}`;
    parts.push(svgEl('text', {
      x: labelW + w + 6, y: y + 17, fill: '#88c0d0',
      'font-size': 11, 'font-weight': 600,
    }, label));
  });

  parts.push('</svg>');
  return parts.join('');
}

// ============ 直方图 ============
function histogram(values, opts = {}) {
  if (!values || !values.length) return '<div class="chart-empty">暂无数据</div>';
  const bins = opts.bins || 8;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);
  const step = range / bins;
  const counts = new Array(bins).fill(0);
  values.forEach(v => {
    const idx = Math.min(Math.floor((v - min) / step), bins - 1);
    counts[idx]++;
  });
  const data = counts.map((c, i) => ({
    label: `${(min + i * step).toFixed(0)}~${(min + (i+1) * step).toFixed(0)}`,
    value: c,
  }));
  return barChart(data, {color: '#a3be8c', height: 220});
}

// ============ 主加载 ============
async function loadAll() {
  try {
    const [s, groups, chainStats, chain, wallets, txs] = await Promise.all([
      fetch('/api/dashboard').then(r => r.json()),
      fetch('/api/by_fingerprint?limit=20').then(r => r.json()),
      fetch('/api/chain_stats').then(r => r.json()).catch(() => null),
      fetch('/api/chain?limit=20').then(r => r.json()).catch(() => []),
      fetch('/api/wallets?limit=20').then(r => r.json()).catch(() => []),
      fetch('/api/transactions?limit=20').then(r => r.json()).catch(() => []),
    ]);
    renderStats(s);
    renderCacheChart(s);
    renderSourceChart(s.by_source);
    renderHourlyChart(s.by_hour);
    renderTopChart(s.top_expr);
    renderDurationChart(s.recent);
    renderHistory(s.recent);
    renderByFingerprint(groups);
    if (chainStats) {
      renderChainStats(chainStats);
      renderChainTip(chain, chainStats);
      renderChainTable(chain);
      renderWallets(wallets);
      renderTxTable(txs);
    }
  } catch(e) {
    document.getElementById('stats-grid').textContent = '加载失败: ' + e;
  }
}

function renderStats(s) {
  const el = document.getElementById('stats-grid');
  const db = s.cache_db || {};
  const mem = s.cache_mem || {};
  const hitRate = ((db.hit_rate || 0) * 100).toFixed(1);
  el.innerHTML = `
    <div class="stat"><div class="n">${s.total}</div><div class="l">总请求</div></div>
    <div class="stat highlight"><div class="n">${db.hits || 0}</div><div class="l">缓存命中</div></div>
    <div class="stat"><div class="n">${db.misses || 0}</div><div class="l">缓存未命中</div></div>
    <div class="stat highlight"><div class="n">${hitRate}%</div><div class="l">累计命中率</div></div>
    <div class="stat"><div class="n">${(s.avg_ms||0).toFixed(0)}ms</div><div class="l">未命中平均耗时</div></div>
    <div class="stat highlight"><div class="n">${(s.avg_cached_ms||0).toFixed(0)}ms</div><div class="l">命中平均耗时</div></div>
    <div class="stat"><div class="n">${mem.size || 0}</div><div class="l">内存缓存条数</div></div>
    <div class="stat"><div class="n">${mem.evictions || 0}</div><div class="l">淘汰次数</div></div>
  `;
}

function renderCacheChart(s) {
  const el = document.getElementById('chart-cache');
  const db = s.cache_db || {};
  const mem = s.cache_mem || {};
  const data = [
    {label: '命中', value: db.hits || 0},
    {label: '未命中', value: db.misses || 0},
  ];
  const hitRate = ((db.hit_rate || 0) * 100).toFixed(1);
  let html = donutChart(data, {
    size: 200, center: hitRate + '%', centerLabel: '累计命中率',
  });
  html += `
    <div style="margin-top:12px;color:#616e88;font-size:11px;
                border-top:1px solid #2e3440;padding-top:10px">
      <div>内存缓存（本次进程）:
        <b style="color:#88c0d0">${mem.size || 0}</b>/${mem.max_size || 0} 条 ·
        TTL ${mem.ttl || 0}s ·
        命中 <b style="color:#a3be8c">${mem.hits || 0}</b> /
        未命中 <b style="color:#d08770">${mem.misses || 0}</b>
        ${mem.total ? `· 实时命中率 <b style="color:#a3be8c">${((mem.hit_rate||0)*100).toFixed(1)}%</b>` : ''}
      </div>
      <div style="margin-top:4px">
        淘汰 <b>${mem.evictions || 0}</b> · 过期 <b>${mem.expired || 0}</b>
      </div>
    </div>
  `;
  el.innerHTML = html;
}

function renderHourlyChart(byHour) {
  const el = document.getElementById('chart-hourly');
  if (!byHour || !byHour.length) {
    el.innerHTML = '<div class="chart-empty">暂无数据</div>';
    return;
  }
  const data = [...byHour].reverse().map(r => ({
    label: (r.hour || '').slice(11, 16),
    value: r.n,
    sub: r.cache_hits || 0,
  }));
  el.innerHTML = dualBarChart(data, {height: 240});
}

function renderSourceChart(bySource) {
  const el = document.getElementById('chart-source');
  if (!bySource || !bySource.length) {
    el.innerHTML = '<div class="chart-empty">暂无数据</div>';
    return;
  }
  const data = bySource.map(r => ({
    label: r.source || 'unknown',
    value: r.n,
  }));
  el.innerHTML = donutChart(data, {size: 200});
}

function renderTopChart(topExpr) {
  const el = document.getElementById('chart-top');
  if (!topExpr || !topExpr.length) {
    el.innerHTML = '<div class="chart-empty">暂无数据</div>';
    return;
  }
  const data = topExpr.map(r => ({
    label: r.expr, value: r.n, sub: r.cache_hits || 0,
  }));
  el.innerHTML = hBarChart(data, {labelW: 140});
}

function renderDurationChart(recent) {
  const el = document.getElementById('chart-duration');
  if (!recent || !recent.length) {
    el.innerHTML = '<div class="chart-empty">暂无数据</div>';
    return;
  }
  const values = recent
    .filter(r => r.ok && r.duration_ms != null && !r.cache_hit)
    .map(r => r.duration_ms);
  if (!values.length) {
    el.innerHTML = '<div class="chart-empty">暂无未命中缓存的耗时数据</div>';
    return;
  }
  el.innerHTML = histogram(values, {bins: 8});
}

// ============ 区块链渲染 ============
function renderChainStats(s) {
  const el = document.getElementById('chain-stats');
  el.innerHTML = `
    <div class="stat gold"><div class="n">${s.height}</div><div class="l">链高度</div></div>
    <div class="stat"><div class="n">${s.next_difficulty}</div><div class="l">当前难度</div></div>
    <div class="stat"><div class="n">${s.total_work.toLocaleString()}</div><div class="l">总工作量</div></div>
    <div class="stat gold"><div class="n">${s.total_reward_calc.toFixed(2)}</div><div class="l">CALC 供应</div></div>
    <div class="stat"><div class="n">${s.wallet_count}</div><div class="l">钱包数</div></div>
    <div class="stat"><div class="n">${s.avg_block_time_s.toFixed(2)}s</div><div class="l">平均出块</div></div>
    <div class="stat gold"><div class="n">$${s.market_cap_usd.toExponential(2)}</div><div class="l">市值（USD）</div></div>
    <div class="stat"><div class="n">$${s.calc_price_usd.toExponential(2)}</div><div class="l">1 CALC 价格</div></div>
  `;
}

function renderChainTip(chain, stats) {
  const el = document.getElementById('chain-tip');
  if (!chain || !chain.length) {
    el.innerHTML = '<span style="color:#616e88;font-size:12px">还没有区块</span>';
    return;
  }
  // 倒序显示，tip 在最右
  const ordered = [...chain].reverse();
  el.innerHTML = ordered.map((b, i) => {
    const cls = b.idx === 0 ? 'genesis' : (b.idx === stats.height ? 'tip' : '');
    const arrow = i < ordered.length - 1 ? '<span class="chain-arrow">→</span>' : '';
    return `<span class="chain-block ${cls}" title="hash: ${b.hash}\nprev: ${b.prev_hash}\nexpr: ${b.expr}=${b.result}\nnonce: ${b.nonce}\ndifficulty: ${b.difficulty}">
      <b>#${b.idx}</b>
      <span class="chain-hash">${b.hash.slice(0, 8)}…</span><br>
      <span style="color:#d8dee9">${escapeHtml(b.expr)}=${escapeHtml(b.result)}</span><br>
      <span class="pow-badge">d=${b.difficulty}</span>
      <span style="color:#616e88"> n=${b.nonce}</span>
    </span>${arrow}`;
  }).join('');
}

function renderChainTable(chain) {
  const tb = document.getElementById('chain-table');
  if (!chain || !chain.length) {
    tb.innerHTML = '<tr><td colspan="5">暂无区块</td></tr>';
    return;
  }
  tb.innerHTML = `
    <tr><th>#</th><th>Hash</th><th>表达式</th><th>难度</th><th>矿工</th></tr>
    ${chain.map(b => `
      <tr title="prev: ${b.prev_hash}&#10;nonce: ${b.nonce}&#10;ts: ${fmtTs(b.timestamp)}">
        <td>${b.idx}</td>
        <td class="chain-hash">${b.hash.slice(0, 10)}…</td>
        <td>${escapeHtml(b.expr)}=${escapeHtml(b.result)}</td>
        <td><span class="pow-badge">d=${b.difficulty}</span></td>
        <td class="miner-addr">${escapeHtml(b.miner)}</td>
      </tr>
    `).join('')}
  `;
}

function renderWallets(wallets) {
  const tb = document.getElementById('wallets-table');
  if (!wallets || !wallets.length) {
    tb.innerHTML = '<tr><td colspan="3">暂无钱包</td></tr>';
    return;
  }
  tb.innerHTML = `
    <tr><th>地址</th><th>余额</th><th>区块 / 尝试</th></tr>
    ${wallets.map(w => `
      <tr>
        <td class="miner-addr">${escapeHtml(w.address)}</td>
        <td style="color:#ebcb8b;font-weight:600">${w.balance.toFixed(2)} CALC</td>
        <td>${w.blocks_mined} / ${(w.total_attempts||0).toLocaleString()}</td>
      </tr>
    `).join('')}
  `;
}

function renderTxTable(txs) {
  const tb = document.getElementById('tx-table');
  if (!txs || !txs.length) {
    tb.innerHTML = '<tr><td colspan="5">暂无交易</td></tr>';
    return;
  }
  tb.innerHTML = `
    <tr><th>时间</th><th>区块</th><th>到</th><th>金额</th><th>类型</th></tr>
    ${txs.map(t => `
      <tr>
        <td>${fmtTsShort(t.ts)}</td>
        <td>#${t.block_idx}</td>
        <td class="miner-addr">${escapeHtml(t.to_addr)}</td>
        <td style="color:#ebcb8b">+${t.amount.toFixed(2)} CALC</td>
        <td><span class="pow-badge">${escapeHtml(t.kind)}</span></td>
      </tr>
    `).join('')}
  `;
}

// ============ 指纹视图 ============
function renderByFingerprint(groups) {
  const el = document.getElementById('by-fp');
  if (!groups || !groups.length) {
    el.innerHTML = '<div style="color:#616e88;font-size:12px">暂无指纹数据（需要经由 app.py 前端发起计算，会带上浏览器指纹）</div>';
    return;
  }

  el.innerHTML = groups.map((g, idx) => {
    const fpShort = g.fingerprint.slice(0, 16);
    const srcTags = (g.by_source || [])
      .map(s => `<span>${escapeHtml(s.source)} × ${s.n}${s.cache_hits ? ` (命中 ${s.cache_hits})` : ''}</span>`)
      .join('');

    const rows = g.records.map(r => `
      <tr>
        <td>${fmtTs(r.ts)}</td>
        <td>${escapeHtml(r.expr)}</td>
        <td>${r.ok ? escapeHtml(r.result) : '<span style="color:#bf616a">失败</span>'}</td>
        <td>${(r.duration_ms||0).toFixed(0)} ms</td>
        <td>${r.cache_hit ? '<span class="cache-badge cache-hit">命中</span>' : '<span class="cache-badge cache-miss">miss</span>'}</td>
        <td>${r.block_idx != null && r.block_idx >= 0 ? `#${r.block_idx}` : '-'}</td>
        <td>${escapeHtml(r.source || '-')}</td>
        <td>${r.trace_id ? r.trace_id.slice(0,8) : '-'}</td>
      </tr>
    `).join('');

    return `
      <details class="fp" id="fp-${idx}">
        <summary>
          <span class="fp-hash">${fpShort}…</span>
          <span class="fp-meta">
            <span>共 <b>${g.n}</b> 次</span>
            <span>命中 <b>${g.cache_hits || 0}</b></span>
            <span>平均 <b>${(g.avg_ms||0).toFixed(0)}ms</b></span>
            <span>最后 ${fmtTs(g.last_ts)}</span>
          </span>
        </summary>
        <div class="fp-body">
          <div class="fp-src">${srcTags}</div>
          <table>
            <thead><tr>
              <th>时间</th><th>表达式</th><th>结果</th><th>耗时</th>
              <th>缓存</th><th>链</th><th>来源</th><th>Trace</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </details>
    `;
  }).join('');
}

function expandAllFp() {
  document.querySelectorAll('details.fp').forEach(d => d.open = true);
}

function collapseAllFp() {
  document.querySelectorAll('details.fp').forEach(d => d.open = false);
}

function renderHistory(rows) {
  const tb = document.querySelector('#history-table tbody');
  if (!rows || !rows.length) {
    tb.innerHTML = '<tr><td colspan="8">暂无记录</td></tr>';
    return;
  }
  tb.innerHTML = rows.map(r => `
    <tr>
      <td>${fmtTs(r.ts)}</td>
      <td>${escapeHtml(r.expr)}</td>
      <td>${escapeHtml(r.result)}</td>
      <td>${(r.duration_ms||0).toFixed(0)} ms</td>
      <td>${r.cache_hit ? '<span class="cache-badge cache-hit">命中</span>' : '<span class="cache-badge cache-miss">miss</span>'}</td>
      <td>${r.block_idx != null && r.block_idx >= 0 ? `<span class="pow-badge">#${r.block_idx}</span>` : '-'}</td>
      <td>${escapeHtml(r.source || '-')}</td>
      <td>${r.trace_id ? r.trace_id.slice(0,8) : '-'}</td>
    </tr>
  `).join('');
}

// ============ 操作 ============
async function quickCalc() {
  const expr = document.getElementById('expr').value.trim();
  if (!expr) return;
  const el = document.getElementById('calc-result');
  el.textContent = '计算中…（含 PoW 挖矿）';
  el.style.color = '#a3be8c';
  try {
    const r = await fetch('/api/calc', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expr}),
    });
    const d = await r.json();
    if (d.ok) {
      const badge = d.cached
        ? ' <span class="cache-badge cache-hit">缓存命中</span>'
        : ' <span class="cache-badge cache-miss">新计算</span>';
      let chainInfo = '';
      if (d.chain && d.chain.block_idx >= 0) {
        chainInfo = ` · <span class="pow-badge">块 #${d.chain.block_idx} 难度 ${d.chain.difficulty} PoW ${d.chain.pow_attempts} 次 ${d.chain.pow_elapsed_ms}ms</span>`;
      }
      el.innerHTML = `<b>${escapeHtml(d.expr)} = ${escapeHtml(d.result)}</b> · ${d.duration_ms} ms · trace ${d.trace_id.slice(0,8)}${badge}${chainInfo}`;
      setTimeout(loadAll, 500);
    } else {
      el.textContent = '错误: ' + d.error;
      el.style.color = '#bf616a';
    }
  } catch(e) {
    el.textContent = '请求失败: ' + e;
    el.style.color = '#bf616a';
  }
}

async function clearCache() {
  if (!confirm('确定清空上游缓存？')) return;
  try {
    const r = await fetch('/api/cache/clear', {method: 'POST'});
    const d = await r.json();
    alert('已清空 ' + d.cleared + ' 条缓存');
    loadAll();
  } catch(e) {
    alert('失败: ' + e);
  }
}

async function verifyChain() {
  const el = document.getElementById('chain-verify-badge');
  el.textContent = '校验中…';
  el.className = '';
  try {
    const r = await fetch('/api/verify_chain');
    const d = await r.json();
    if (d.ok) {
      el.innerHTML = `<span class="chain-status-ok">✓ 链完整 · ${d.info.blocks} 块 · 高度 ${d.info.height}</span>`;
    } else {
      el.innerHTML = `<span class="chain-status-bad">✗ 链在 #${d.info.broken_at} 损坏: ${escapeHtml(d.info.reason)}</span>`;
    }
  } catch(e) {
    el.innerHTML = `<span class="chain-status-bad">校验失败: ${escapeHtml(String(e))}</span>`;
  }
}

document.getElementById('expr').addEventListener('keydown', e => {
  if (e.key === 'Enter') quickCalc();
});

loadAll();
verifyChain();
setInterval(loadAll, 15000);
</script>
"""


# ============ 路由 ============
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if check_password(pw):
            session["logged_in"] = True
            session["user"] = "admin"
            return redirect(url_for("dashboard"))
        return render_template_string(LOGIN_HTML, error="密码错误"), 401
    return render_template_string(LOGIN_HTML, error="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template_string(
        DASH_HTML,
        user=session.get("user", "admin"),
        upstream=CALC_API,
    )


@app.route("/refresh")
@login_required
def refresh():
    return redirect(url_for("dashboard"))


# ---- 统计 ----
@app.route("/api/dashboard")
@login_required
def api_dashboard():
    try:
        return jsonify(fetch_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/by_fingerprint")
@login_required
def api_by_fingerprint():
    try:
        limit = min(int(request.args.get("limit", 20)), 200)
    except ValueError:
        limit = 20
    try:
        return jsonify(fetch_by_fingerprint(limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/calc", methods=["POST"])
@login_required
def api_calc():
    data = request.get_json(silent=True) or {}
    expr = (data.get("expr") or "").strip()
    if not expr:
        return jsonify({"ok": False, "error": "missing expr"}), 400
    try:
        status, payload = run_calc(expr, notify=False)
        return jsonify(payload), status
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/api/history")
@login_required
def api_history():
    try:
        limit = min(int(request.args.get("limit", 100)), 1000)
        return jsonify(fetch_history(limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ---- 缓存 ----
@app.route("/api/cache")
@login_required
def api_cache():
    try:
        return jsonify(fetch_cache())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/cache/clear", methods=["POST"])
@login_required
def api_cache_clear():
    try:
        return jsonify(clear_cache())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ---- 区块链 ----
@app.route("/api/chain_stats")
@login_required
def api_chain_stats():
    try:
        return jsonify(fetch_chain_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/chain")
@login_required
def api_chain():
    try:
        limit = min(int(request.args.get("limit", 30)), 200)
        return jsonify(fetch_chain(limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/wallets")
@login_required
def api_wallets():
    try:
        limit = min(int(request.args.get("limit", 20)), 200)
        return jsonify(fetch_wallets(limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/transactions")
@login_required
def api_transactions():
    try:
        limit = min(int(request.args.get("limit", 20)), 200)
        return jsonify(fetch_transactions(limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/verify_chain")
@login_required
def api_verify_chain():
    try:
        return jsonify(verify_chain())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    print(f"[sdk] http://127.0.0.1:{SDK_PORT}/")
    print(f"[sdk] calc API: {CALC_API}")
    print(f"[sdk] 密码文件: {os.path.abspath(PW_FILE)}")
    print(f"[sdk] 如需改密码，删除 {PW_FILE} 或设 ADMIN_PASSWORD")
    app.run(host="127.0.0.1", port=SDK_PORT, debug=False, threaded=True)