# -*- coding: utf-8 -*-
"""回归：中继对上游必须"同一时刻只有一条连接"。

假代理只服务一条流（真实 kaiser 代理实测一次只服务一条，并发会互相挤死）。
测试让泵持续持有一条连接，然后触发一次旁路（大跨度 Range）。
修复前：旁路与泵长时间并发（overlap 数百 ms）→ FAIL。
修复后：旁路先让泵断开，只有极短的收尾重叠（<100ms）→ PASS。
"""
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOTAL = 64 * 1024 * 1024
MAX_OK_OVERLAP = 0.10          # 允许的收尾重叠（秒）
_state = {"active": 0, "max": 0, "overlap": 0.0, "multi_since": 0.0,
          "events": []}
_lock = threading.Lock()
T0 = time.time()


def byte_at(i):
    return (i * 37 + 11) % 251


class SerialProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"     # 真实 kaiser 代理是 HTTP/1.1 keep-alive

    def do_GET(self):
        with _lock:
            if _state["active"] > 0:
                _state["multi_since"] = time.time()
            _state["active"] += 1
            _state["max"] = max(_state["max"], _state["active"])
        m = re.match(r"bytes=(\d+)-", self.headers.get("Range") or "")
        s = int(m.group(1)) if m else 0
        with _lock:
            _state["events"].append(("open", s, time.time()))
        try:
            self.send_response(206)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Content-Range",
                             "bytes %d-%d/%d" % (s, TOTAL - 1, TOTAL))
            self.send_header("Content-Length", str(TOTAL - s))
            self.end_headers()
            off = s
            while off < TOTAL:
                n = min(65536, TOTAL - off)
                self.wfile.write(bytes(byte_at(off + k) for k in range(n)))
                self.wfile.flush()
                off += n
                time.sleep(0.02)       # 慢喂，保持连接存活
        except Exception:
            pass
        finally:
            with _lock:
                if _state["active"] > 1:
                    _state["overlap"] += time.time() - _state["multi_since"]
                    _state["multi_since"] = time.time()
                _state["active"] -= 1
                _state["events"].append(("close", s, time.time()))

    def log_message(self, *a):
        pass


def main():
    up = ThreadingHTTPServer(("127.0.0.1", 18998), SerialProxy)
    threading.Thread(target=up.serve_forever, daemon=True).start()

    import ring_relay
    ring_relay.CHUNK = 1 << 18
    ring_relay.LOOKBACK = 1 << 20
    ring_relay.BYPASS_CHUNK = 1 << 20
    ring_relay.RING_MAX = 256 << 20
    ring_relay.ensure_started(port=18093, log=lambda m: None)
    pump = ring_relay.set_target("http://127.0.0.1:18998/kaiser",
                                 lambda m: None)
    pump.start()
    time.sleep(0.6)                    # 让泵建立并持有连接

    import http.client
    c = http.client.HTTPConnection("127.0.0.1", 18093, timeout=30)
    c.request("GET", "/stream", headers={
        "Range": "bytes=%d-%d" % (50 * 1024 * 1024, 50 * 1024 * 1024 + 65535)})
    r = c.getresponse()
    d = r.read()
    c.close()
    print("bypass: status=%s got=%d" % (r.status, len(d)))
    time.sleep(0.3)

    for ev in _state["events"]:
        print("  event %-5s start=%-9d t=%.3f" % (ev[0], ev[1], ev[2]))
    print("max_active=%d overlap=%.3fs" % (_state["max"], _state["overlap"]))
    ok = _state["overlap"] < MAX_OK_OVERLAP and len(d) > 0
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
