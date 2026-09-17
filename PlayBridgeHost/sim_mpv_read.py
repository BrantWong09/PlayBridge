# -*- coding: utf-8 -*-
"""模拟 mpv/ffmpeg 的读法打中继：一条连接一个 Range 请求，读完（服务端
Content-Length 到了就结束）再发下一条 `bytes=<pos>-`，看在哪里断。
用法: python sim_mpv_read.py [目标MB] [单次等待秒]
"""
import http.client
import re
import sys
import time
import urllib.parse

TARGET_MB = int(sys.argv[1]) if len(sys.argv) > 1 else 200
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 20
RELAY = "http://127.0.0.1:18095/stream"
UA = "Lavf/63.6.100"

p = urllib.parse.urlsplit(RELAY)
pos = 0
target = TARGET_MB << 20
t0 = time.time()
reqs = 0
empty = 0
while pos < target:
    reqs += 1
    try:
        conn = http.client.HTTPConnection(p.hostname, p.port, timeout=TIMEOUT)
        conn.request("GET", p.path, headers={
            "Range": "bytes=%d-" % pos, "User-Agent": UA, "Accept": "*/*"})
        r = conn.getresponse()
    except Exception as e:
        print("[%6.1fs] #%d bytes=%d- 建连/请求失败: %r" % (
            time.time() - t0, reqs, pos, e))
        break
    cr = r.getheader("Content-Range")
    cl = r.getheader("Content-Length")
    n = 0
    try:
        while True:
            chunk = r.read(1 << 18)
            if not chunk:
                break
            n += len(chunk)
    except Exception as e:
        print("[%6.1fs] #%d bytes=%d- 读中断(%r) 已读 %d" % (
            time.time() - t0, reqs, pos, e, n))
        conn.close()
        break
    conn.close()
    m = re.match(r"bytes (\d+)-(\d+)/(\d+)", cr or "")
    print("[%6.1fs] #%d Range=%d- → %s %s len=%s 实读=%d %.1fMB/s" % (
        time.time() - t0, reqs, pos, r.status, cr, cl, n,
        n / 1048576 / max(0.001, time.time() - t0)))
    if n == 0:
        empty += 1
        print("        ^^^ 空响应！位置 %d (前一次 %d)" % (pos, pos))
        if empty >= 2:
            print("连续空响应，停止")
            break
    pos += n
    if n < 4096:
        print("        响应过小(%d)，下一轮" % n)

print("结束: %d 个请求, 读到 %d 字节 (%.1fMB) 用时 %.1fs" % (
    reqs, pos, pos / 1048576, time.time() - t0))
