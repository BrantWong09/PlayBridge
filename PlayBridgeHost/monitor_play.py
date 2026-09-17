# -*- coding: utf-8 -*-
"""连续采样 mpv 播放进度。用法: python monitor_play.py [总秒数] [间隔秒数]"""
import json
import sys
import time

PIPE = r"\\.\pipe\playbridge_mpv"
TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 90
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 10
PROPS = ["time-pos", "demuxer-cache-time", "pause", "core-idle",
         "eof-reached", "cache-buffering-state"]


def sample():
    try:
        f = open(PIPE, "r+b", buffering=0)
    except OSError as e:
        return {"_err": "连不上 IPC: %r" % e}
    for i, p in enumerate(PROPS, 1):
        f.write((json.dumps({"command": ["get_property", p],
                             "request_id": i}) + "\n").encode())
    f.flush()
    got, t0 = {}, time.time()
    while len(got) < len(PROPS) and time.time() - t0 < 5:
        line = f.readline()
        if not line:
            break
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("request_id"):
            got[m["request_id"]] = m
    f.close()
    out = {}
    for i, p in enumerate(PROPS, 1):
        m = got.get(i) or {}
        out[p] = m.get("data") if m.get("error") == "success" else None
    return out


t0 = time.time()
print("%6s %10s %10s %6s %6s %6s %6s" % (
    "t(s)", "time-pos", "cache(s)", "pause", "idle", "eof", "buf%"))
while time.time() - t0 < TOTAL:
    s = sample()
    if "_err" in s:
        print("%6.0f %s" % (time.time() - t0, s["_err"]))
    else:
        print("%6.0f %10s %10s %6s %6s %6s %6s" % (
            time.time() - t0, s["time-pos"], s["demuxer-cache-time"],
            s["pause"], s["core-idle"], s["eof-reached"],
            s["cache-buffering-state"]))
    time.sleep(STEP)
