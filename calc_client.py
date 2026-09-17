#!/usr/bin/env python3
# calc_client.py — OTel + 查看服务 + API + SQLite + 缓存 + 哈希链 + PoW（缓存命中也上链）
import os
import re
import sys
import json
import time
import html
import hashlib
import sqlite3
import logging
import argparse
import threading
from collections import OrderedDict
from contextlib import contextmanager

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
    from flask import Flask, jsonify, request
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


BASE = "http://127.0.0.1:5002/v1/chat/completions"
MODEL = "ai-v9-artificialretard"
OP_MAP = {"+": "+", "-": "-", "*": "×", "/": "÷", ".": "."}
KIND_COLOR = {
    "CLIENT": "#4a90d9", "INTERNAL": "#7ab87a",
    "SERVER": "#d97a4a", "PRODUCER": "#b07ad9", "CONSUMER": "#d9b07a",
}
PROPAGATOR = TraceContextTextMapPropagator()
_KIND_MAP = {"INTERNAL": 1, "SERVER": 2, "CLIENT": 3, "PRODUCER": 4, "CONSUMER": 5}

logging.getLogger("werkzeug").setLevel(logging.ERROR)


# ============================================================
# 区块链核心
# ============================================================
class Block:
    __slots__ = ("index", "prev_hash", "expr", "result", "nonce",
                 "timestamp", "difficulty", "miner", "reward", "trace_id",
                 "cached", "hash")

    def __init__(self, index, prev_hash, expr, result, nonce,
                 timestamp, difficulty, miner, reward, trace_id="",
                 cached=False):
        self.index = index
        self.prev_hash = prev_hash
        self.expr = expr
        self.result = result
        self.nonce = nonce
        self.timestamp = timestamp
        self.difficulty = difficulty
        self.miner = miner
        self.reward = reward
        self.trace_id = trace_id
        self.cached = cached
        self.hash = self.compute_hash()

    def compute_hash(self):
        """完整字段参与 hash——结果、难度、矿工都不可篡改"""
        payload = "|".join([
            str(self.index), self.prev_hash, self.expr, str(self.result),
            str(self.nonce), f"{self.timestamp:.6f}", str(self.difficulty),
            self.miner, f"{self.reward:.8f}", self.trace_id,
            "1" if self.cached else "0",
        ])
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_dict(self):
        return {
            "index": self.index, "hash": self.hash, "prev_hash": self.prev_hash,
            "expr": self.expr, "result": self.result, "nonce": self.nonce,
            "timestamp": self.timestamp, "difficulty": self.difficulty,
            "miner": self.miner, "reward": self.reward,
            "trace_id": self.trace_id, "cached": self.cached,
        }


# CALC 代币单价（美元）
CALC_PRICE_USD = 0.00000001
# 缓存命中时的奖励折扣系数
CACHED_REWARD_FACTOR = 0.5


class Blockchain:
    def __init__(self, db_path, base_difficulty=3, target_block_time=1.0,
                 adjust_every=5, max_difficulty=5):
        self.db_path = db_path
        self.base_difficulty = base_difficulty
        self.target_block_time = target_block_time
        self.adjust_every = adjust_every
        self.max_difficulty = max_difficulty
        self._lock = threading.Lock()
        self._init_tables()
        self._init_genesis()

    @contextmanager
    def lock(self):
        self._lock.acquire()
        try:
            yield
        finally:
            self._lock.release()

    def _init_tables(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS blocks (
                idx INTEGER PRIMARY KEY,
                hash TEXT NOT NULL UNIQUE,
                prev_hash TEXT NOT NULL,
                expr TEXT NOT NULL,
                result TEXT,
                nonce INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                difficulty INTEGER NOT NULL,
                miner TEXT,
                reward REAL NOT NULL,
                trace_id TEXT
            );
            CREATE TABLE IF NOT EXISTS wallets (
                address TEXT PRIMARY KEY,
                balance REAL NOT NULL DEFAULT 0,
                blocks_mined INTEGER NOT NULL DEFAULT 0,
                total_attempts INTEGER NOT NULL DEFAULT 0,
                first_seen REAL,
                last_seen REAL
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_idx INTEGER NOT NULL,
                ts REAL NOT NULL,
                from_addr TEXT,
                to_addr TEXT NOT NULL,
                amount REAL NOT NULL,
                kind TEXT NOT NULL,
                trace_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_blocks_hash ON blocks(hash);
            CREATE INDEX IF NOT EXISTS idx_tx_addr ON transactions(to_addr);
        """)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(blocks)").fetchall()}
        if "cached" not in cols:
            conn.execute("ALTER TABLE blocks ADD COLUMN cached INTEGER DEFAULT 0")
            print("[chain] migrated: added blocks.cached")
        conn.commit()
        conn.close()

    def _init_genesis(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        if cur.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0:
            g = Block(
                index=0, prev_hash="0" * 64, expr="GENESIS", result="CALC",
                nonce=0, timestamp=time.time(), difficulty=1,
                miner="system", reward=0.0, cached=False,
            )
            cur.execute(
                "INSERT INTO blocks "
                "(idx, hash, prev_hash, expr, result, nonce, timestamp, "
                " difficulty, miner, reward, trace_id, cached) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (g.index, g.hash, g.prev_hash, g.expr, g.result, g.nonce,
                 g.timestamp, g.difficulty, g.miner, g.reward, g.trace_id,
                 1 if g.cached else 0)
            )
            conn.commit()
        conn.close()

    def last_block(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM blocks ORDER BY idx DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def next_difficulty(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT idx, timestamp, difficulty FROM blocks "
            "ORDER BY idx DESC LIMIT ?",
            (self.adjust_every,)
        ).fetchall()
        conn.close()

        if len(rows) < self.adjust_every:
            return rows[0]["difficulty"] if rows else self.base_difficulty

        newest, oldest = rows[0], rows[-1]
        if newest["idx"] % self.adjust_every != 0:
            return newest["difficulty"]

        elapsed = newest["timestamp"] - oldest["timestamp"]
        avg = elapsed / (len(rows) - 1) if len(rows) > 1 else elapsed

        cur_d = newest["difficulty"]
        if avg < self.target_block_time * 0.5:
            return min(cur_d + 1, self.max_difficulty)
        elif avg > self.target_block_time * 2.0:
            return max(cur_d - 1, 1)
        return cur_d

    def mine(self, expr, result, miner="unknown", cached=False,
             trace_id="", max_attempts=5_000_000):
        """
        挖矿：完整字段参与 hash。
        result 已知，difficulty 按链自动调整，nonce 是唯一变量。
        返回 dict（含已构造好的 Block 对象）。
        """
        last = self.last_block()
        prev_hash = last["hash"] if last else "0" * 64
        idx = (last["idx"] + 1) if last else 0
        difficulty = self.next_difficulty()
        base_reward = 1.0 + (difficulty - 1) * 0.5
        reward = base_reward * (CACHED_REWARD_FACTOR if cached else 1.0)
        timestamp = time.time()
        prefix = "0" * difficulty

        nonce = 0
        t0 = time.perf_counter()
        while nonce < max_attempts:
            b = Block(
                index=idx, prev_hash=prev_hash, expr=expr, result=result,
                nonce=nonce, timestamp=timestamp, difficulty=difficulty,
                miner=miner, reward=reward, trace_id=trace_id, cached=cached,
            )
            if b.hash.startswith(prefix):
                return {
                    "block": b,
                    "nonce": nonce,
                    "difficulty": difficulty,
                    "reward": reward,
                    "attempts": nonce + 1,
                    "elapsed_s": time.perf_counter() - t0,
                    "prev_hash": prev_hash,
                    "index": idx,
                }
            nonce += 1
        return None

    def add_block(self, expr, result, miner="unknown",
                  trace_id="", pow_result=None, cached=False):
        """把已挖好的区块入库"""
        if pow_result is None or "block" not in pow_result:
            return None

        block = pow_result["block"]

        # 校验：挖矿时的链尾必须还是当前链尾
        last = self.last_block()
        cur_prev = last["hash"] if last else "0" * 64
        if block.prev_hash != cur_prev:
            return None

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO blocks "
            "(idx, hash, prev_hash, expr, result, nonce, timestamp, "
            " difficulty, miner, reward, trace_id, cached) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (block.index, block.hash, block.prev_hash, block.expr,
             block.result, block.nonce, block.timestamp, block.difficulty,
             block.miner, block.reward, block.trace_id,
             1 if block.cached else 0)
        )
        cur.execute("""
            INSERT INTO wallets (address, balance, blocks_mined,
                                 total_attempts, first_seen, last_seen)
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                balance = balance + ?,
                blocks_mined = blocks_mined + 1,
                total_attempts = total_attempts + ?,
                last_seen = ?
        """, (miner, block.reward, pow_result["attempts"], block.timestamp,
              block.timestamp, block.reward, pow_result["attempts"],
              block.timestamp))
        cur.execute(
            "INSERT INTO transactions "
            "(block_idx, ts, from_addr, to_addr, amount, kind, trace_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (block.index, block.timestamp, None, miner, block.reward,
             "reward_cached" if block.cached else "reward", trace_id)
        )
        conn.commit()
        conn.close()

        d = block.to_dict()
        d["attempts"] = pow_result["attempts"]
        d["elapsed_s"] = pow_result["elapsed_s"]
        return d

    def verify(self):
        """全链校验。返回 (ok, info)"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM blocks ORDER BY idx ASC").fetchall()
        conn.close()

        if not rows:
            return True, {"blocks": 0, "broken_at": None}

        prev = None
        for r in rows:
            cached_val = r["cached"] if "cached" in r.keys() else 0
            b = Block(
                index=r["idx"], prev_hash=r["prev_hash"], expr=r["expr"],
                result=r["result"], nonce=r["nonce"], timestamp=r["timestamp"],
                difficulty=r["difficulty"], miner=r["miner"],
                reward=r["reward"], trace_id=r["trace_id"] or "",
                cached=bool(cached_val),
            )
            if b.hash != r["hash"]:
                return False, {"blocks": len(rows), "broken_at": r["idx"],
                               "reason": "hash mismatch",
                               "expected": b.hash, "actual": r["hash"]}
            if prev is not None and r["prev_hash"] != prev["hash"]:
                return False, {"blocks": len(rows), "broken_at": r["idx"],
                               "reason": "prev_hash mismatch"}
            # 创世块跳过 PoW
            if r["idx"] > 0 and not r["hash"].startswith("0" * r["difficulty"]):
                return False, {"blocks": len(rows), "broken_at": r["idx"],
                               "reason": "pow invalid"}
            prev = dict(r)

        return True, {
            "blocks": len(rows), "broken_at": None,
            "tip": prev["hash"], "height": prev["idx"],
        }

    def chain(self, limit=50):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM blocks ORDER BY idx DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_wallet(self, address):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM wallets WHERE address = ?", (address,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def all_wallets(self, limit=50):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM wallets ORDER BY balance DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def transactions(self, limit=50):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def chain_stats(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        total_blocks = cur.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        height = max(total_blocks - 1, 0)
        mined = cur.execute(
            "SELECT COUNT(*) FROM blocks WHERE idx > 0"
        ).fetchone()[0]
        cached_blocks = cur.execute(
            "SELECT COUNT(*) FROM blocks WHERE idx > 0 AND cached=1"
        ).fetchone()[0]

        total_reward = cur.execute(
            "SELECT SUM(reward) FROM blocks WHERE idx > 0"
        ).fetchone()[0] or 0
        total_attempts = cur.execute(
            "SELECT SUM(w.total_attempts) FROM wallets w"
        ).fetchone()[0] or 0

        avg_difficulty = cur.execute(
            "SELECT AVG(difficulty) FROM blocks WHERE idx > 0"
        ).fetchone()[0] or 0
        max_difficulty = cur.execute(
            "SELECT MAX(difficulty) FROM blocks"
        ).fetchone()[0] or 0

        rows = cur.execute(
            "SELECT timestamp FROM blocks ORDER BY idx DESC LIMIT 11"
        ).fetchall()
        times = [r["timestamp"] for r in rows]
        if len(times) >= 2:
            intervals = [times[i] - times[i+1] for i in range(len(times)-1)]
            avg_block_time = sum(intervals) / len(intervals)
        else:
            avg_block_time = 0

        wallet_count = cur.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
        conn.close()

        market_cap_usd = total_reward * CALC_PRICE_USD

        return {
            "height": height,
            "total_blocks": total_blocks,
            "mined_blocks": mined,
            "cached_blocks": cached_blocks,
            "total_work": total_attempts,
            "total_reward_calc": total_reward,
            "avg_difficulty": avg_difficulty,
            "max_difficulty": max_difficulty,
            "next_difficulty": self.next_difficulty(),
            "avg_block_time_s": avg_block_time,
            "wallet_count": wallet_count,
            "calc_price_usd": CALC_PRICE_USD,
            "market_cap_usd": market_cap_usd,
            "cached_reward_factor": CACHED_REWARD_FACTOR,
        }


# ============ 缓存 ============
class CalcCache:
    def __init__(self, ttl=300, max_size=1000):
        self.ttl = ttl
        self.max_size = max_size
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = self.evictions = self.expired = 0

    def get(self, expr):
        with self._lock:
            item = self._cache.get(expr)
            if item is None:
                self.misses += 1
                return None
            result, ts = item
            if time.time() - ts > self.ttl:
                del self._cache[expr]
                self.expired += 1
                self.misses += 1
                return None
            self._cache.move_to_end(expr)
            self.hits += 1
            return result

    def set(self, expr, result):
        with self._lock:
            if expr in self._cache:
                self._cache.move_to_end(expr)
                self._cache[expr] = (result, time.time())
            else:
                self._cache[expr] = (result, time.time())
                if len(self._cache) > self.max_size:
                    self._cache.popitem(last=False)
                    self.evictions += 1

    def stats(self):
        with self._lock:
            total = self.hits + self.misses
            return {
                "hits": self.hits, "misses": self.misses, "total": total,
                "hit_rate": (self.hits / total) if total else 0.0,
                "size": len(self._cache), "max_size": self.max_size,
                "ttl": self.ttl, "evictions": self.evictions,
                "expired": self.expired,
            }

    def keys(self):
        with self._lock:
            now = time.time()
            return [{"expr": k, "result": v[0], "age_s": round(now - v[1], 1)}
                    for k, v in self._cache.items()]

    def clear(self):
        with self._lock:
            n = len(self._cache)
            self._cache.clear()
            return n


CACHE = CalcCache()


# ============ 全局链实例 ============
DB_PATH = os.environ.get("CALC_DB", "calc_stats.db")
CHAIN = Blockchain(DB_PATH)


# ============ 通知 ============
def send_notify(title, body, timeout=3):
    payload = {"cmd": "termux-notification", "args": ["-t", title, "-c", body]}
    text = f"/tool termux_api {json.dumps(payload, ensure_ascii=False)}"
    try:
        r = requests.post(
            BASE,
            json={"model": MODEL, "messages": [{"role": "user", "content": text}]},
            timeout=(timeout, timeout),
        )
        r.raise_for_status()
        return True, None
    except requests.exceptions.Timeout:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


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


# ============ calc_history 表 ============
def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calc_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, trace_id TEXT NOT NULL, expr TEXT NOT NULL,
            result TEXT, duration_ms REAL, ok INTEGER,
            client_ip TEXT, user_agent TEXT, fingerprint TEXT, source TEXT
        )
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(calc_history)")}
    if "cache_hit" not in cols:
        conn.execute("ALTER TABLE calc_history ADD COLUMN cache_hit INTEGER DEFAULT 0")
        print("[db] migrated: added cache_hit")
    if "block_idx" not in cols:
        conn.execute("ALTER TABLE calc_history ADD COLUMN block_idx INTEGER DEFAULT -1")
        print("[db] migrated: added block_idx")
    for idx in ("idx_ts:ts", "idx_trace:trace_id", "idx_source:source",
                "idx_fp:fingerprint", "idx_cache:cache_hit"):
        name, col = idx.split(":")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON calc_history({col})")
    conn.commit()
    conn.close()


def record_calc(trace_id, expr, result, duration_ms, ok,
                client_ip="", user_agent="", fingerprint="", source="local",
                cache_hit=False, block_idx=-1):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO calc_history "
            "(ts, trace_id, expr, result, duration_ms, ok, client_ip, "
            " user_agent, fingerprint, source, cache_hit, block_idx) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), trace_id, expr, result, duration_ms, 1 if ok else 0,
             client_ip, user_agent, fingerprint, source,
             1 if cache_hit else 0, block_idx),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[db] record_calc failed: {e}", file=sys.stderr)


def query_stats():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(*) FROM calc_history").fetchone()[0]
    ok_count = cur.execute("SELECT COUNT(*) FROM calc_history WHERE ok=1").fetchone()[0]
    fail_count = total - ok_count

    cache_hit_count = cur.execute(
        "SELECT COUNT(*) FROM calc_history WHERE cache_hit=1"
    ).fetchone()[0]
    cache_miss_count = total - cache_hit_count

    def _row(q):
        r = cur.execute(q).fetchone()
        return (r[0] or 0, r[1] or 0, r[2] or 0)

    avg_ms, min_ms, max_ms = _row(
        "SELECT AVG(duration_ms), MIN(duration_ms), MAX(duration_ms) "
        "FROM calc_history WHERE ok=1 AND cache_hit=0"
    )
    avg_c, min_c, max_c = _row(
        "SELECT AVG(duration_ms), MIN(duration_ms), MAX(duration_ms) "
        "FROM calc_history WHERE ok=1 AND cache_hit=1"
    )

    by_source = [dict(r) for r in cur.execute(
        "SELECT source, COUNT(*) AS n, AVG(duration_ms) AS avg_ms, "
        "SUM(cache_hit) AS cache_hits FROM calc_history GROUP BY source"
    ).fetchall()]
    by_hour = [dict(r) for r in cur.execute(
        "SELECT strftime('%Y-%m-%d %H:00', ts, 'unixepoch', 'localtime') AS hour, "
        "COUNT(*) AS n, SUM(cache_hit) AS cache_hits FROM calc_history "
        "GROUP BY hour ORDER BY hour DESC LIMIT 24"
    ).fetchall()]
    top_expr = [dict(r) for r in cur.execute(
        "SELECT expr, COUNT(*) AS n, SUM(cache_hit) AS cache_hits "
        "FROM calc_history GROUP BY expr ORDER BY n DESC LIMIT 10"
    ).fetchall()]
    recent = [dict(r) for r in cur.execute(
        "SELECT ts, trace_id, expr, result, duration_ms, ok, source, "
        "client_ip, cache_hit, block_idx FROM calc_history "
        "ORDER BY ts DESC LIMIT 50"
    ).fetchall()]
    conn.close()

    return {
        "total": total, "ok": ok_count, "fail": fail_count,
        "avg_ms": avg_ms, "min_ms": min_ms, "max_ms": max_ms,
        "avg_cached_ms": avg_c, "min_cached_ms": min_c, "max_cached_ms": max_c,
        "by_source": by_source, "by_hour": by_hour,
        "top_expr": top_expr, "recent": recent,
        "cache_db": {
            "hits": cache_hit_count, "misses": cache_miss_count,
            "total": total,
            "hit_rate": cache_hit_count / total if total else 0.0,
        },
        "cache_mem": CACHE.stats(),
    }


def query_by_fingerprint(limit_per_fp=20):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    fps = cur.execute("""
        SELECT fingerprint, COUNT(*) AS n, AVG(duration_ms) AS avg_ms,
               SUM(cache_hit) AS cache_hits, MAX(ts) AS last_ts
        FROM calc_history WHERE fingerprint != ''
        GROUP BY fingerprint ORDER BY last_ts DESC
    """).fetchall()
    result = []
    for fp in fps:
        rows = cur.execute("""
            SELECT ts, trace_id, expr, result, duration_ms, ok, source,
                   client_ip, cache_hit, block_idx
            FROM calc_history WHERE fingerprint = ?
            ORDER BY ts DESC LIMIT ?
        """, (fp["fingerprint"], limit_per_fp)).fetchall()
        by_src = [dict(r) for r in cur.execute(
            "SELECT source, COUNT(*) AS n, SUM(cache_hit) AS cache_hits "
            "FROM calc_history WHERE fingerprint = ? GROUP BY source",
            (fp["fingerprint"],)
        ).fetchall()]
        result.append({
            "fingerprint": fp["fingerprint"], "n": fp["n"],
            "avg_ms": fp["avg_ms"], "cache_hits": fp["cache_hits"] or 0,
            "last_ts": fp["last_ts"], "by_source": by_src,
            "records": [dict(r) for r in rows],
        })
    conn.close()
    return result


_init_db()


# ============ OTLP 导出 ============
def _attr_value(v):
    if isinstance(v, bool): return {"boolValue": v}
    if isinstance(v, int): return {"intValue": str(v)}
    if isinstance(v, float): return {"doubleValue": v}
    if isinstance(v, str): return {"stringValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_attr_value(x) for x in v]}}
    return {"stringValue": str(v)}


def _attrs_to_otlp(attrs):
    return [{"key": k, "value": _attr_value(v)} for k, v in (attrs or {}).items()]


def _span_to_otlp(s):
    d = {
        "traceId": format(s.context.trace_id, "032x"),
        "spanId": format(s.context.span_id, "016x"),
        "name": s.name, "kind": _KIND_MAP.get(s.kind.name, 1),
        "startTimeUnixNano": str(s.start_time),
        "endTimeUnixNano": str(s.end_time),
        "attributes": _attrs_to_otlp(s.attributes),
        "status": {"code": 1 if s.status.status_code.name == "OK" else 2},
    }
    if s.parent is not None and s.parent.is_valid:
        d["parentSpanId"] = format(s.parent.span_id, "016x")
    if s.events:
        d["events"] = [{"name": e.name, "timeUnixNano": str(e.timestamp),
                        "attributes": _attrs_to_otlp(e.attributes)} for e in s.events]
    return d


def export_otlp(exporter, path):
    traces = exporter.get_traces()
    all_spans, resource_attrs = [], []
    for spans in traces.values():
        if spans and not resource_attrs:
            resource_attrs = _attrs_to_otlp(spans[0].resource.attributes)
        for s in spans:
            all_spans.append(_span_to_otlp(s))
    payload = {"resourceSpans": [{
        "resource": {"attributes": resource_attrs},
        "scopeSpans": [{"scope": {"name": "calc_client", "version": "1.0.0"},
                        "spans": all_spans}],
    }]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return len(traces), len(all_spans)


# ============ OTel 初始化 ============
def setup_otel(otlp_endpoint=None, service_name="calc-client"):
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(MEM))
    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint and HAS_OTLP:
        provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
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
    if not isinstance(ui, dict): return None
    if ui.get("type") == "button" and ui.get("label") == label:
        return (ui.get("action") or {}).get("event")
    for child in ui.get("children", []) or []:
        ev = find_event(child, label)
        if ev: return ev
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
    if p is None or not p.is_valid: return ""
    return format(p.span_id, "016x")


def _render_trace_html(tid, spans):
    if not spans: return "<p>空 trace</p>"
    spans_sorted = sorted(spans, key=lambda s: (s.start_time, -s.end_time))
    min_start = min(s.start_time for s in spans)
    max_end = max(s.end_time for s in spans)
    total = max(max_end - min_start, 1)
    by_id = {s.context.span_id: s for s in spans}

    def depth(s):
        d = 0; p = s.parent
        while p is not None and p.is_valid and p.span_id in by_id:
            d += 1; p = by_id[p.span_id].parent
        return d

    rows = []
    for s in spans_sorted:
        left = (s.start_time - min_start) / total * 100
        width = max((s.end_time - s.start_time) / total * 100, 0.3)
        dur = fmt_ms(s.end_time - s.start_time)
        color = KIND_COLOR.get(s.kind.name, "#888")
        attrs = " ".join(f"{k}={v}" for k, v in s.attributes.items() if len(str(v)) < 40)
        rows.append(f"""
          <div class="row" style="padding-left:{depth(s)*22}px">
            <div class="label"><b>{html.escape(s.name)}</b>
              <span class="id">{format(s.context.span_id,'016x')[:8]}</span>
              <span class="kind" style="color:{color}">{s.kind.name.lower()}</span>
            </div>
            <div class="bar-wrap"><div class="bar"
              style="left:{left:.3f}%;width:{width:.3f}%;background:{color}">{dur}</div></div>
            <div class="attrs">{html.escape(attrs)}</div>
          </div>""")
    return f"""
    <div class="meta">trace {tid[:16]} · 总 {fmt_ms(max_end-min_start)} · {len(spans)} spans</div>
    {''.join(rows)}"""


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
.attrs{color:#616e88;font-size:11px;margin-top:2px;white-space:nowrap;overflow:hidden}
.bar-wrap{position:relative;height:18px;background:#1c2027;border-radius:3px;overflow:hidden}
.bar{position:absolute;height:100%;top:0;border-radius:3px;color:#fff;font-size:11px;line-height:18px;padding-left:6px}
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
            rows.append(f"""<tr><td><a href="/trace/{tid}">{tid[:16]}</a></td>
              <td>{html.escape(roots[0].name if roots else '?')}</td>
              <td>{len(spans)}</td><td>{fmt_ms(max_e-min_s)}</td></tr>""")
        return f"""<!doctype html><meta charset=utf-8><style>{CSS}</style>
        <h1>Traces ({len(traces)})</h1>
        <table><tr><th>Trace ID</th><th>Root</th><th>Spans</th><th>Duration</th></tr>
        {''.join(rows) if rows else '<tr><td colspan=4>还没有 trace</td></tr>'}
        </table>"""

    @app.route("/trace/<tid>")
    def trace_view(tid):
        traces = exporter.get_traces()
        matched = [k for k in traces if k.startswith(tid)]
        if not matched: return f"trace {tid} not found", 404
        full_tid = matched[0]
        return f"""<!doctype html><meta charset=utf-8><style>{CSS}</style>
        <h1><a href="/">←</a> trace {full_tid[:16]}</h1>
        {_render_trace_html(full_tid, traces[full_tid])}"""

    @app.route("/api/traces")
    def api_traces():
        out = []
        for tid, spans in exporter.get_traces().items():
            min_s = min(s.start_time for s in spans)
            max_e = max(s.end_time for s in spans)
            out.append({"trace_id": tid, "duration_ms": (max_e-min_s)/1e6,
                        "span_count": len(spans)})
        return jsonify(out)

    return app


# ============ 对外 API ============
def build_api_app(client_factory, exporter):
    app = Flask("calc_api")
    API_TOKEN = os.environ.get("CALC_API_TOKEN")

    def _check_auth():
        if not API_TOKEN: return True
        return request.headers.get("Authorization", "") == f"Bearer {API_TOKEN}"

    def _run_calc(expr, notify):
        parent_ctx = PROPAGATOR.extract(request.headers)
        tracer = otel_trace.get_tracer("calc_api")
        t0 = time.perf_counter()

        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        user_agent = request.headers.get("User-Agent", "")
        fingerprint = request.headers.get("X-Fingerprint", "")
        source = request.headers.get("X-Source", "api")
        miner_addr = f"{source}:{fingerprint[:8] if fingerprint else 'default'}"

        with tracer.start_as_current_span(
            "api.calc", context=parent_ctx, kind=SpanKind.SERVER
        ) as sp:
            sp.set_attribute("http.method", request.method)
            sp.set_attribute("http.route", request.path)
            sp.set_attribute("http.client_ip", client_ip)
            sp.set_attribute("calc.expr", expr)
            sp.set_attribute("calc.notify", notify)
            sp.set_attribute("calc.source", source)
            sp.set_attribute("chain.miner", miner_addr)
            if fingerprint:
                sp.set_attribute("client.fingerprint", fingerprint[:32])

            try:
                with CHAIN.lock():
                    # ============ 1. 缓存查找 ============
                    cached = CACHE.get(expr)

                    # ============ 2. 得到结果（缓存或真算） ============
                    if cached is not None:
                        result = cached
                        cache_hit = True
                    else:
                        c = client_factory()
                        result, _ = c.calc(expr, notify=False, quiet=True,
                                           record=False)
                        CACHE.set(expr, result)
                        cache_hit = False

                    trace_id_pre = format(sp.get_span_context().trace_id, "032x")

                    # ============ 3. 挖矿（result 已知，完整字段参与 hash） ============
                    with tracer.start_as_current_span("pow", kind=SpanKind.INTERNAL) as psp:
                        pow_result = CHAIN.mine(
                            expr, result, miner=miner_addr,
                            cached=cache_hit, trace_id=trace_id_pre,
                        )
                        if pow_result is None:
                            raise RuntimeError("PoW 挖矿失败")
                        psp.set_attribute("pow.difficulty", pow_result["difficulty"])
                        psp.set_attribute("pow.nonce", pow_result["nonce"])
                        psp.set_attribute("pow.attempts", pow_result["attempts"])
                        psp.set_attribute("pow.elapsed_ms",
                                          pow_result["elapsed_s"] * 1000)
                        psp.set_attribute("pow.cached", cache_hit)

                    # ============ 4. 上链 ============
                    block_info = CHAIN.add_block(
                        expr, result, miner=miner_addr,
                        trace_id=trace_id_pre, pow_result=pow_result,
                        cached=cache_hit,
                    )
                    block_idx = block_info["index"] if block_info else -1
                    if block_info:
                        sp.set_attribute("chain.block_idx", block_info["index"])
                        sp.set_attribute("chain.block_hash",
                                         block_info["hash"][:16])
                        sp.set_attribute("chain.reward", block_info["reward"])
                        sp.set_attribute("chain.difficulty",
                                         block_info["difficulty"])
                        sp.set_attribute("chain.cached", cache_hit)

                dur_ms = (time.perf_counter() - t0) * 1000
                ctx = sp.get_span_context()
                trace_id = format(ctx.trace_id, "032x")
                sp.set_attribute("calc.result", result)
                sp.set_attribute("calc.duration_ms", dur_ms)
                sp.set_attribute("cache.hit", cache_hit)

                notify_ok = notify_err = None
                if notify:
                    with tracer.start_as_current_span("notify", kind=SpanKind.INTERNAL) as nsp:
                        suffix = " (缓存)" if cache_hit else ""
                        notify_ok, notify_err = send_notify(
                            "计算结果", f"{expr} = {result}{suffix}"
                        )
                        nsp.set_attribute("notify.ok", bool(notify_ok))
                        nsp.set_attribute("notify.from_cache", cache_hit)
                        if notify_err:
                            nsp.set_attribute("notify.error", notify_err)

                record_calc(trace_id, expr, result, dur_ms, True,
                            client_ip, user_agent, fingerprint, source,
                            cache_hit=cache_hit, block_idx=block_idx)

                return jsonify({
                    "ok": True, "expr": expr, "result": result,
                    "duration_ms": round(dur_ms, 2),
                    "cached": cache_hit, "notified": notify_ok,
                    "notify_error": notify_err,
                    "trace_id": trace_id,
                    "trace_url": f"http://127.0.0.1:{VIEWER_PORT}/trace/{trace_id}",
                    "chain": {
                        "block_idx": block_idx,
                        "cached": cache_hit,
                        "pow_attempts": pow_result["attempts"],
                        "pow_elapsed_ms": round(pow_result["elapsed_s"] * 1000, 2),
                        "difficulty": pow_result["difficulty"],
                        "reward": block_info.get("reward") if block_info else None,
                    },
                })

            except ValueError as e:
                dur_ms = (time.perf_counter() - t0) * 1000
                sp.set_status(Status(StatusCode.ERROR, str(e)))
                record_calc("", expr, "", dur_ms, False,
                            client_ip, user_agent, fingerprint, source)
                return jsonify({"ok": False, "error": str(e)}), 400
            except Exception as e:
                dur_ms = (time.perf_counter() - t0) * 1000
                sp.set_status(Status(StatusCode.ERROR, str(e)))
                sp.record_exception(e)
                record_calc("", expr, "", dur_ms, False,
                            client_ip, user_agent, fingerprint, source)
                return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/health")
    def health():
        return jsonify({
            "ok": True, "service": "calc-client", "model": MODEL, "upstream": BASE,
            "cache": {"enabled": True, "ttl": CACHE.ttl, "max_size": CACHE.max_size},
            "chain": {"enabled": True, "db": os.path.abspath(DB_PATH)},
        })

    @app.route("/api/calc", methods=["POST"])
    def api_calc_post():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        expr = (data.get("expr") or data.get("expression") or "").strip()
        if not expr: return jsonify({"ok": False, "error": "missing expr"}), 400
        return _run_calc(expr, bool(data.get("notify", False)))

    @app.route("/api/calc", methods=["GET"])
    def api_calc_get():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        expr = (request.args.get("expr") or "").strip()
        if not expr: return jsonify({"ok": False, "error": "missing expr"}), 400
        notify = request.args.get("notify", "0") in ("1", "true", "yes")
        return _run_calc(expr, notify)

    @app.route("/api/export", methods=["POST"])
    def api_export():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        path = data.get("path") or f"traces-{int(time.time())}.json"
        try:
            n_t, n_s = export_otlp(exporter, path)
            return jsonify({"ok": True, "path": os.path.abspath(path),
                            "traces": n_t, "spans": n_s,
                            "size": os.path.getsize(path)})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/stats")
    def api_stats():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        try: return jsonify(query_stats())
        except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/history")
    def api_history():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        try: limit = min(int(request.args.get("limit", 100)), 1000)
        except ValueError: limit = 100
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM calc_history ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
            conn.close()
            return jsonify([dict(r) for r in rows])
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/by_fingerprint")
    def api_by_fingerprint():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        try: limit = min(int(request.args.get("limit", 20)), 200)
        except ValueError: limit = 20
        try: return jsonify(query_by_fingerprint(limit))
        except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/cache")
    def api_cache():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        return jsonify({"stats": CACHE.stats(), "keys": CACHE.keys()})

    @app.route("/api/cache/clear", methods=["POST"])
    def api_cache_clear():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        return jsonify({"ok": True, "cleared": CACHE.clear()})

    @app.route("/api/chain")
    def api_chain():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        try: limit = min(int(request.args.get("limit", 50)), 500)
        except ValueError: limit = 50
        return jsonify(CHAIN.chain(limit))

    @app.route("/api/chain/stats")
    def api_chain_stats():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        try: return jsonify(CHAIN.chain_stats())
        except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/chain/verify")
    def api_chain_verify():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        ok, info = CHAIN.verify()
        return jsonify({"ok": ok, "info": info})

    @app.route("/api/wallets")
    def api_wallets():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        try: limit = min(int(request.args.get("limit", 50)), 500)
        except ValueError: limit = 50
        return jsonify(CHAIN.all_wallets(limit))

    @app.route("/api/wallet/<path:addr>")
    def api_wallet(addr):
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        w = CHAIN.get_wallet(addr)
        if not w: return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify(w)

    @app.route("/api/transactions")
    def api_tx():
        if not _check_auth(): return jsonify({"ok": False, "error": "unauthorized"}), 401
        try: limit = min(int(request.args.get("limit", 50)), 500)
        except ValueError: limit = 50
        return jsonify(CHAIN.transactions(limit))

    return app


# ============ 服务启动 ============
def start_app(app, host, port, label):
    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False,
                               use_reloader=False, threaded=True),
        daemon=True,
    )
    t.start()
    print(f"[{label}] http://{host}:{port}/")
    return t


# ============ 客户端 ============
class CalcClient:
    def __init__(self, tracer):
        self.tracer = tracer
        self.messages = []
        self.history = []

    def _do_request(self, text):
        self.messages.append({"role": "user", "content": text})
        r = requests.post(BASE, json={"model": MODEL, "messages": self.messages},
                          timeout=30)
        r.raise_for_status()
        with self.tracer.start_as_current_span("json.parse", kind=SpanKind.INTERNAL) as sp:
            sp.set_attribute("resp.size", len(r.text))
            content = r.json()["choices"][0]["message"]["content"]
            sp.set_attribute("content.length", len(content))
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
        with self.tracer.start_as_current_span("notify", kind=SpanKind.INTERNAL) as sp:
            sp.set_attribute("notify.title", title)
            ok, err = send_notify(title, body)
            sp.set_attribute("notify.ok", bool(ok))
            if err:
                sp.set_attribute("notify.error", err)
                sp.set_status(Status(StatusCode.ERROR, err))

    def calc(self, expression, notify=True, quiet=False, record=True):
        t0 = time.perf_counter()
        if not notify:
            cached = CACHE.get(expression)
            if cached is not None:
                total = time.perf_counter() - t0
                if record:
                    record_calc("", expression, cached, total * 1000, True,
                                source="local", cache_hit=True)
                if not quiet:
                    print(f"  {expression} = {cached}   [{total*1000:.1f} ms] (cached)")
                return cached, total

        with self.tracer.start_as_current_span("calc", kind=SpanKind.INTERNAL) as root:
            root.set_attribute("calc.expr", expression)
            with self.tracer.start_as_current_span("start", kind=SpanKind.INTERNAL):
                ui = self._do_request("calc")
            ui = self.click("C", ui)
            for ch in expression:
                if ch.isspace(): continue
                if ch.isdigit(): ui = self.click(ch, ui)
                elif ch in OP_MAP: ui = self.click(OP_MAP[ch], ui)
                else: raise ValueError(f"不支持的字符: {ch!r}")
            ui = self.click("=", ui)
            result = get_display(ui)
            root.set_attribute("calc.result", result)
            ctx = root.get_span_context()
            trace_id = format(ctx.trace_id, "032x")
            total = time.perf_counter() - t0
            if not notify:
                CACHE.set(expression, result)
            if record:
                self.history.append({"trace_id": trace_id, "expr": expression,
                                     "result": result, "total": total})
                record_calc(trace_id, expression, result, total * 1000, True,
                            source="local", cache_hit=False)
            if not quiet:
                print(f"  {expression} = {result}   [{total*1000:.0f} ms]   "
                      f"trace={trace_id[:8]}")
                print(f"  viewer: http://127.0.0.1:{VIEWER_PORT}/trace/{trace_id}")
            if notify:
                self.notify("计算结果", f"{expression} = {result}")
            return result, total

    def summary(self):
        if not self.history:
            print("还没有计算记录")
            return
        n = len(self.history)
        avg = sum(h["total"] for h in self.history) / n
        print(f"\n共 {n} 次计算，平均 {avg*1000:.0f} ms")


# ============ 主程序 ============
VIEWER_PORT = 8899
API_PORT = 8900


def main():
    global VIEWER_PORT, API_PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--otlp")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--api-port", type=int, default=8900)
    ap.add_argument("--api-host", default="127.0.0.1")
    ap.add_argument("--no-api", action="store_true")
    ap.add_argument("--service", default="calc-client")
    ap.add_argument("--cache-ttl", type=int, default=300)
    ap.add_argument("--cache-size", type=int, default=1000)
    ap.add_argument("--pow-difficulty", type=int, default=3)
    ap.add_argument("expr", nargs="*")
    args = ap.parse_args()
    VIEWER_PORT = args.port
    API_PORT = args.api_port
    CACHE.ttl = args.cache_ttl
    CACHE.max_size = args.cache_size
    CHAIN.base_difficulty = args.pow_difficulty

    tracer = setup_otel(args.otlp, args.service)
    start_app(build_viewer_app(MEM), "127.0.0.1", args.port, "viewer")
    if not args.no_api and HAS_FLASK:
        api_app = build_api_app(lambda: CalcClient(tracer), MEM)
        start_app(api_app, args.api_host, args.api_port, "api")

    cs = CHAIN.chain_stats()
    print(f"[stats] DB: {os.path.abspath(DB_PATH)}")
    print(f"[cache] TTL={CACHE.ttl}s  max_size={CACHE.max_size}")
    print(f"[chain] height={cs['height']}  difficulty={cs['next_difficulty']}  "
          f"supply={cs['total_reward_calc']:.2f} CALC  "
          f"cached_blocks={cs['cached_blocks']}  "
          f"市值=${cs['market_cap_usd']:.10f}")

    if not args.no_api and HAS_FLASK:
        print()
        print("  对外 API：")
        print(f"    POST http://{args.api_host}:{args.api_port}/api/calc")
        print(f"    GET  http://{args.api_host}:{args.api_port}/api/chain")
        print(f"    GET  http://{args.api_host}:{args.api_port}/api/chain/stats")
        print(f"    GET  http://{args.api_host}:{args.api_port}/api/chain/verify")
        print(f"    GET  http://{args.api_host}:{args.api_port}/api/wallets")
        print(f"    GET  http://{args.api_host}:{args.api_port}/api/transactions")
        print()

    client = CalcClient(tracer)

    if args.expr:
        client.calc("".join(args.expr), notify=False)
        client.summary()
        print("\n服务保持运行，Ctrl+C 退出")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt: pass
        return

    print("输入表达式，空行退出。")
    print("命令：:chain  :verify  :wallet  :pow <n>  :export  :quit\n")
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not line.strip(): break
        line = line.strip()

        if line == ":chain":
            cs = CHAIN.chain_stats()
            print(f"  高度={cs['height']}  难度={cs['next_difficulty']}  "
                  f"总工作={cs['total_work']}  缓存块={cs['cached_blocks']}")
            print(f"  供应={cs['total_reward_calc']:.4f} CALC  "
                  f"钱包={cs['wallet_count']}  市值=${cs['market_cap_usd']:.10f}")
            for b in CHAIN.chain(5):
                tag = " [缓存]" if b.get("cached") else ""
                print(f"    #{b['idx']} {b['hash'][:12]}… "
                      f"{b['expr']}={b['result']} d={b['difficulty']} "
                      f"nonce={b['nonce']}{tag} by {b['miner']}")
            continue

        if line == ":verify":
            ok, info = CHAIN.verify()
            if ok:
                print(f"  ✓ 链完整，共 {info['blocks']} 个块，高度 {info['height']}")
                print(f"    tip: {info['tip'][:16]}…")
            else:
                print(f"  ✗ 链在区块 #{info['broken_at']} 损坏: {info['reason']}")
            continue

        if line == ":wallet":
            for w in CHAIN.all_wallets(10):
                print(f"    {w['address']:<32} {w['balance']:>8.2f} CALC  "
                      f"({w['blocks_mined']} 块)")
            continue

        if line.startswith(":pow"):
            arg = line[4:].strip()
            try:
                d = int(arg)
                CHAIN.base_difficulty = d
                print(f"  PoW 难度设为 {d}")
            except ValueError:
                print(f"  当前难度: {CHAIN.base_difficulty}")
            continue

        if line.startswith(":export"):
            arg = line[len(":export"):].strip() or f"traces-{int(time.time())}.json"
            try:
                n_t, n_s = export_otlp(MEM, arg)
                print(f"  已导出 {n_t} 条 trace、{n_s} 个 span → {os.path.abspath(arg)}")
            except Exception as e:
                print(f"  导出失败: {e}")
            continue

        if line == ":quit":
            break

        try:
            client.calc(line, notify=False)
        except Exception as e:
            print(f"错误: {e}")


if __name__ == "__main__":
    main()