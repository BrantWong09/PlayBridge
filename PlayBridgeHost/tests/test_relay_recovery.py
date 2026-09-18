# -*- coding: utf-8 -*-
"""Regression: the pump must recover on its own after a transient upstream outage.

Fake proxy serves short streams (then goes silent to force rotation). For a
window it accepts connections but never responds (pump build times out). Once
that window passes, the pump must resume fetching WITHOUT a new set_target().

Before the fix the pump sets dead=True after MAX_STRIKES and never comes back,
so read_pos stays frozen -> FAIL.
"""
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOTAL = 16 * 1024 * 1024
FEED = 256 * 1024
DOWN_UNTIL = [0.0]


def byte_at(i):
    return (i * 37 + 11) % 251


class FlakyKaiser(BaseHTTPRequestHandler):
    def do_GET(self):
        if time.time() < DOWN_UNTIL[0]:
            time.sleep(5)          # accept, never answer -> client timeout
            return
        m = re.match(r"bytes=(\d+)-", self.headers.get("Range") or "")
        s = int(m.group(1)) if m else 0
        self.send_response(206)
        self.send_header("Content-Type", "video/x-matroska")
        self.send_header("Content-Range",
                         "bytes %d-%d/%d" % (s, TOTAL - 1, TOTAL))
        self.send_header("Content-Length", str(TOTAL - s))
        self.end_headers()
        off = s
        end = min(TOTAL, s + FEED)
        try:
            while off < end:
                n = min(131072, end - off)
                self.wfile.write(bytes(byte_at(off + k) for k in range(n)))
                off += n
                self.wfile.flush()
            time.sleep(30)         # go silent -> forces a rotation
        except Exception:
            pass

    def log_message(self, *a):
        pass


def main():
    up = ThreadingHTTPServer(("127.0.0.1", 18997), FlakyKaiser)
    threading.Thread(target=up.serve_forever, daemon=True).start()

    import ring_relay
    ring_relay.SILENT = 1.0
    ring_relay.ROTATE = 1 << 20
    ring_relay.MAX_STRIKES = 2
    ring_relay.RING_MAX = 4 << 20
    ring_relay.LOOKBACK = 1 << 20

    pump = ring_relay.set_target("http://127.0.0.1:18997/kaiser",
                                 lambda m: print("LOG", m))
    pump.start()

    t0 = time.time()
    while pump.read_pos < 512 * 1024 and time.time() - t0 < 8:
        time.sleep(0.05)
    before = pump.read_pos
    print("primed read_pos=%d" % before)

    DOWN_UNTIL[0] = time.time() + 8.0      # outage outlasts strike budget

    time.sleep(10.0)                        # let it fail and (old code) die
    print("during/after outage: dead=%s read_pos=%d" % (pump.dead, pump.read_pos))

    resumed = False
    t1 = time.time()
    while time.time() - t1 < 20:
        if (not pump.dead) and pump.read_pos > before + 128 * 1024:
            resumed = True
            break
        time.sleep(0.1)
    print("RESULT:", "PASS" if resumed else "FAIL",
          "dead=%s read_pos=%d" % (pump.dead, pump.read_pos))
    return 0 if resumed else 1


if __name__ == "__main__":
    raise SystemExit(main())
