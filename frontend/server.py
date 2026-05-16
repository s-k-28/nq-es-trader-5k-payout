#!/usr/bin/env python3
"""Dashboard server — serves trade data + static frontend for the 12-model strategy."""
import http.server
import json
import csv
import os

PORT = 8080
BASE = os.path.join(os.path.dirname(__file__), '..')
TRADES_CSV = os.path.join(BASE, 'trades_backtest_full.csv')
CHARTS_DIR = BASE


def load_trades(path):
    trades = []
    if not os.path.exists(path):
        return trades
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append({
                'entry_time': row['entry_time'],
                'exit_time': row.get('exit_time', ''),
                'direction': row['direction'],
                'model': row['model'],
                'tag': row.get('tag', ''),
                'entry': float(row['entry']),
                'exit': float(row['exit']),
                'stop': float(row['stop']),
                'target': float(row['target']),
                'reason': row['reason'],
                'risk_ticks': float(row['risk_ticks']),
                'total_r': float(row['total_r']),
            })
    return trades


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(__file__), **kwargs)

    def do_GET(self):
        if self.path == '/api/trades':
            self._json_response(load_trades(TRADES_CSV))
            return
        if self.path.startswith('/charts/'):
            filename = self.path.replace('/charts/', '')
            filepath = os.path.join(CHARTS_DIR, filename)
            if os.path.exists(filepath) and filepath.endswith('.png'):
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.end_headers()
                with open(filepath, 'rb') as f:
                    self.wfile.write(f.read())
                return
        return super().do_GET()

    def _json_response(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


if __name__ == '__main__':
    print(f"Dashboard running at http://localhost:{PORT}")
    server = http.server.HTTPServer(('', PORT), Handler)
    server.serve_forever()
