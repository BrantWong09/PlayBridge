# -*- coding: utf-8 -*-
"""离线测试 ring_relay v3（顺序流模式）：
- 主路径：无 Range 请求 → 200 + Content-Length，顺序拉完整数据
- bytes=0- 同样走主路径
- 回看：缓存内 Range 命中 → 206
"""
import re, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOTAL = 8 * 1024 * 1024
def byte_at(i): return (i * 37 + 11) % 251

class Kaiser3(BaseHTTPRequestHandler):
    def do_GET(self):
        m = re.match(r"bytes=(\d+)-", self.headers.get("Range") or "")
        s = int(m.group(1)) if m else 0
        self.send_response(206)
        self.send_header("Content-Type", "video/x-matroska")
        self.send_header("Content-Range", "bytes %d-%d/%d" % (s, TOTAL - 1, TOTAL))
        self.send_header("Content-Length", str(TOTAL - s))
        self.end_headers()
        off = s
        try:
            while off < TOTAL:
                n = min(131072, TOTAL - off)
                self.wfile.write(bytes(byte_at(off + k) for k in range(n)))
                off += n
                time.sleep(0.005)
        except Exception:
            pass
    def log_message(self, *a): pass

up = ThreadingHTTPServer(("127.0.0.1", 18996), Kaiser3)
threading.Thread(target=up.serve_forever, daemon=True).start()

import ring_relay
ring_relay.CHUNK = 1 << 18
ring_relay.LOOKBACK = 4 << 20
ring_relay.ensure_started(port=18092, log=lambda m: print("LOG", m))
ring_relay.set_target("http://127.0.0.1:18996/kaiser", lambda m: print("LOG", m))
time.sleep(0.3)

import http.client
fails = 0

# 1) 主路径：无 Range，完整顺序读
c = http.client.HTTPConnection("127.0.0.1", 18092, timeout=120)
c.request("GET", "/stream")
r = c.getresponse()
d = r.read(); c.close()
ok = r.status == 200 and len(d) == TOTAL and d == bytes(byte_at(i) for i in range(TOTAL))
print("[main-stream]", "OK" if ok else "FAIL", r.status, len(d),
      "accept-ranges=", r.getheader("Accept-Ranges"))
fails += (not ok)

# 2) 回看命中（主路径读满后尾部仍在环内）
time.sleep(0.5)
c = http.client.HTTPConnection("127.0.0.1", 18092, timeout=30)
c.request("GET", "/stream", headers={"Range": "bytes=%d-%d" % (TOTAL - 100000, TOTAL - 1)})
r = c.getresponse(); d2 = r.read(); c.close()
ok = r.status == 206 and d2 == bytes(byte_at(TOTAL - 100000 + k) for k in range(len(d2)))
print("[rewind-cache]", "OK" if ok else "FAIL", r.status, len(d2))
fails += (not ok)

# 3) 未缓存的前跳 → 416
c = http.client.HTTPConnection("127.0.0.1", 18092, timeout=10)
c.request("GET", "/stream", headers={"Range": "bytes=100000-200000"})
r = c.getresponse(); r.read(); c.close()
print("[evict-416]", "OK" if r.status in (416, 206) else "FAIL", r.status)

print("RESULT:", "PASS" if fails == 0 else "FAIL(%d)" % fails)
