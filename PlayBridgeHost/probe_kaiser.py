# -*- coding: utf-8 -*-
"""测 kaiser 代理经 adb 隧道的行为：建流耗时(TTFB)、持续喂数据节奏、静默间隔。
用法: python probe_kaiser.py [持续秒数]
"""
import http.client
import json
import sys
import time
import urllib.parse

UA = ("com.android.chrome/131.0.6778.200 (Linux;Android 10) "
      "AndroidXMedia3/1.5.1")
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def main():
    url = open("probe_url.txt", encoding="utf-8").read().strip()
    p = urllib.parse.urlsplit(url)
    path = p.path + ("?" + p.query if p.query else "")

    t0 = time.time()
    conn = http.client.HTTPConnection(p.hostname, p.port, timeout=60)
    conn.request("GET", path, headers={
        "User-Agent": UA, "Accept": "*/*", "Range": "bytes=0-",
        "Icy-MetaData": "1"})
    r = conn.getresponse()
    print("TTFB(含请求): %.1fs  status=%s %s  type=%s len=%s" % (
        time.time() - t0, r.status, r.reason,
        r.getheader("Content-Type"), r.getheader("Content-Length")))

    total = 0
    win_start = time.time()
    win_bytes = 0
    last_data = time.time()
    max_gap = 0.0
    print("%6s %10s %10s %8s" % ("t(s)", "win(KB/s)", "tot(MB)", "gap(s)"))
    while time.time() - t0 < DURATION:
        try:
            chunk = r.read(1 << 16)
        except Exception as e:
            print("读异常 @%.1fs: %r" % (time.time() - t0, e))
            break
        now = time.time()
        if not chunk:
            print("EOF @%.1fs  累计 %.1fMB" % (now - t0, total / 1048576))
            break
        total += len(chunk)
        win_bytes += len(chunk)
        max_gap = max(max_gap, now - last_data)
        last_data = now
        if now - win_start >= 5.0:
            print("%6.1f %10.0f %10.1f %8.2f" % (
                now - t0, win_bytes / 1024 / (now - win_start),
                total / 1048576, max_gap))
            win_start, win_bytes = now, 0

    dt = time.time() - t0
    print("结束: %.1fs 共 %.1fMB 平均 %.0f KB/s 最大静默间隔 %.1fs" % (
        dt, total / 1048576, total / 1024 / dt, max_gap))
    conn.close()


if __name__ == "__main__":
    main()
