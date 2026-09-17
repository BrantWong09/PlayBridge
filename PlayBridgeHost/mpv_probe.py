# -*- coding: utf-8 -*-
"""查 mpv 播放状态：连 --input-ipc-server 命名的管道，读几个关键属性。
用法: python mpv_probe.py
"""
import json
import time

PIPE = r"\\.\pipe\playbridge_mpv"
PROPS = ["time-pos", "duration", "pause", "core-idle", "eof-reached",
         "demuxer-cache-time", "cache-buffering-state", "video-format",
         "width", "height", "cache-speed", "file-format"]


def main():
    try:
        f = open(PIPE, "r+b", buffering=0)
    except OSError as e:
        print("连不上 IPC 管道 %s: %r" % (PIPE, e))
        return
    cmds = [["get_property", p] for p in PROPS]
    for i, c in enumerate(cmds, 1):
        f.write((json.dumps({"command": c, "request_id": i}) + "\n").encode())
    f.flush()

    got = {}
    t0 = time.time()
    while len(got) < len(cmds) and time.time() - t0 < 5:
        line = f.readline()
        if not line:
            break
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        rid = msg.get("request_id")
        if rid:
            got[rid] = msg
    f.close()

    for i, p in enumerate(PROPS, 1):
        m = got.get(i)
        if m is None:
            print("  %-24s <无响应>" % p)
        elif m.get("error") != "success":
            print("  %-24s error=%s" % (p, m.get("error")))
        else:
            print("  %-24s %s" % (p, m.get("data")))


if __name__ == "__main__":
    main()
