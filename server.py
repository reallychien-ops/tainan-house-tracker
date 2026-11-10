#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台南購屋雷達 Web Server (http://localhost:8765)
提供：
- GET /              -> 互動式地圖與購屋總表網頁 (index.html)
- GET /api/data      -> 讀取最新 properties.json 與巡檢紀錄
- POST /api/track    -> 刪除（停止追蹤）或還原某物件，並即時同步回 Google 試算表
- POST /api/check    -> 立即執行今日查價（檢查降價/漲價/下架＋搜尋新物件）並同步 Google 試算表
"""

import os
import json
from http.server import HTTPServer, SimpleHTTPRequestHandler
import daily_tracker

BASE_DIR = "/Users/chien/Antigravity/tainan_house_tracker"
PROP_FILE = os.path.join(BASE_DIR, "properties.json")
LOG_FILE = os.path.join(BASE_DIR, "daily_check_log.json")


class TrackerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/data"):
            with open(PROP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            logs = []
            if os.path.exists(LOG_FILE):
                try:
                    with open(LOG_FILE, "r", encoding="utf-8") as lf:
                        logs = json.load(lf)
                except Exception:
                    pass
            data["logs"] = logs
            return self._send_json(data)
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        payload = json.loads(raw) if raw else {}

        if self.path.startswith("/api/track"):
            prop_id = payload.get("id")
            tracked = bool(payload.get("tracked", False))
            sync_sheet = bool(payload.get("sync_sheet", True))
            with open(PROP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for p in data.get("properties", []):
                if p["id"] == prop_id:
                    p["tracked"] = tracked
                    p["status"] = "上架追蹤中" if tracked else "已手動排除(不追蹤)"
                    break
            with open(PROP_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if sync_sheet:
                try:
                    daily_tracker.sync_to_google_sheet(data)
                except Exception as e:
                    return self._send_json({"ok": True, "warning": str(e)})
            return self._send_json({"ok": True, "id": prop_id, "tracked": tracked})

        if self.path.startswith("/api/check"):
            scan_new = bool(payload.get("scan_new", True))
            log_entry = daily_tracker.run_daily_check(scan_new=scan_new)
            with open(PROP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._send_json({"ok": True, "result": log_entry, "data": data})

        return self._send_json({"error": "Not found"}, status=404)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    server = HTTPServer(("127.0.0.1", port), TrackerHandler)
    print(f"Tainan House Tracker running at http://localhost:{port}")
    server.serve_forever()
