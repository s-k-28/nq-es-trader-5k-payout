#!/usr/bin/env python3
"""Central hub — receives heartbeats and trades from all bots, serves live dashboard."""
import http.server
import json
import sqlite3
import os
import time
import threading

PORT = int(os.getenv('HUB_PORT', '9090'))
DB_PATH = os.path.join(os.path.dirname(__file__), 'hub.db')

lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            last_seen REAL,
            total_trades INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            model TEXT,
            direction TEXT,
            entry REAL,
            stop REAL,
            target REAL,
            risk_ticks REAL,
            contracts INTEGER,
            total_r REAL,
            pnl_usd REAL,
            reason TEXT,
            entry_time TEXT,
            exit_time TEXT,
            daily_r REAL,
            daily_pnl REAL,
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id);
        CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(created_at);
    """)
    conn.commit()
    conn.close()


class HubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path == '/api/heartbeat':
            body = self._read_body()
            uid = body.get('user_id', 'anonymous')
            with lock:
                conn = get_db()
                conn.execute(
                    "INSERT INTO users (user_id, last_seen) VALUES (?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET last_seen=?",
                    (uid, time.time(), time.time()))
                conn.commit()
                conn.close()
            self._json({'ok': True})

        elif self.path == '/api/trade':
            body = self._read_body()
            uid = body.get('user_id', 'anonymous')
            with lock:
                conn = get_db()
                conn.execute(
                    "INSERT INTO trades (user_id, model, direction, entry, stop, target, "
                    "risk_ticks, contracts, total_r, pnl_usd, reason, entry_time, exit_time, "
                    "daily_r, daily_pnl, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, body.get('model'), body.get('direction'),
                     body.get('entry'), body.get('stop'), body.get('target'),
                     body.get('risk_ticks'), body.get('contracts'),
                     body.get('total_r'), body.get('pnl_usd'),
                     body.get('reason'), body.get('entry_time'), body.get('exit_time'),
                     body.get('daily_r'), body.get('daily_pnl'), time.time()))
                conn.execute(
                    "INSERT INTO users (user_id, last_seen, total_trades, total_pnl) "
                    "VALUES (?, ?, 1, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "last_seen=?, total_trades=total_trades+1, total_pnl=total_pnl+?",
                    (uid, time.time(), body.get('pnl_usd', 0),
                     time.time(), body.get('pnl_usd', 0)))
                conn.commit()
                conn.close()
            self._json({'ok': True})
        else:
            self._json({'error': 'not found'}, 404)

    def do_GET(self):
        if self.path == '/api/users':
            now = time.time()
            conn = get_db()
            rows = conn.execute(
                "SELECT user_id, last_seen, total_trades, total_pnl FROM users "
                "ORDER BY last_seen DESC").fetchall()
            conn.close()
            users = [{
                'user_id': r['user_id'],
                'online': (now - r['last_seen']) < 90,
                'last_seen': r['last_seen'],
                'total_trades': r['total_trades'],
                'total_pnl': round(r['total_pnl'], 2),
            } for r in rows]
            self._json(users)

        elif self.path.startswith('/api/trades'):
            uid = None
            if '?' in self.path:
                params = dict(p.split('=') for p in self.path.split('?')[1].split('&') if '=' in p)
                uid = params.get('user')
            conn = get_db()
            if uid:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE user_id=? ORDER BY created_at DESC LIMIT 200",
                    (uid,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY created_at DESC LIMIT 500").fetchall()
            conn.close()
            trades = [{k: r[k] for k in r.keys()} for r in rows]
            self._json(trades)

        elif self.path == '/api/stats':
            conn = get_db()
            now = time.time()
            online = conn.execute(
                "SELECT COUNT(*) as c FROM users WHERE ?-last_seen < 90",
                (now,)).fetchone()['c']
            today_start = time.time() - 86400
            today = conn.execute(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(pnl_usd),0) as pnl, "
                "COALESCE(AVG(total_r),0) as avgr FROM trades WHERE created_at > ?",
                (today_start,)).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(pnl_usd),0) as pnl FROM trades"
            ).fetchone()
            conn.close()
            self._json({
                'online_users': online,
                'today_trades': today['cnt'],
                'today_pnl': round(today['pnl'], 2),
                'today_avg_r': round(today['avgr'], 3),
                'total_trades': total['cnt'],
                'total_pnl': round(total['pnl'], 2),
            })

        elif self.path == '/' or self.path == '/index.html':
            fpath = os.path.join(os.path.dirname(__file__), 'index.html')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self._cors_headers()
            self.end_headers()
            with open(fpath, 'rb') as f:
                self.wfile.write(f.read())
        else:
            self._json({'error': 'not found'}, 404)


if __name__ == '__main__':
    init_db()
    print(f"Hub running at http://0.0.0.0:{PORT}")
    server = http.server.HTTPServer(('', PORT), HubHandler)
    server.serve_forever()
