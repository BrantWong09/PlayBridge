# -*- coding: utf-8 -*-
"""观察一轮播放：进度 / 缓存 / 轮换次数 / 模拟器侧代理与影视仓 CPU / 是否被杀。
用法: python watch_session.py [观察秒数] [采样间隔秒]
"""
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "playbridge.log")   # 应用始终以 UTF-8 写此文件
PIPE = r"\\.\pipe\playbridge_mpv"
ADB = r"C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe"
DEV = "127.0.0.1:16416"

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 270
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 30


def props(names):
    try:
        f = open(PIPE, "r+b", buffering=0)
    except OSError:
        return {}
    out = {}
    for i, n in enumerate(names, 1):
        f.write((json.dumps({"command": ["get_property", n],
                             "request_id": i}) + "\n").encode())
    got = {}
    t0 = time.time()
    while len(got) < len(names) and time.time() - t0 < 4:
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
    for i, n in enumerate(names, 1):
        m = got.get(i) or {}
        out[n] = m.get("data") if m.get("error") == "success" else None
    return out


def adb(args, timeout=20):
    try:
        r = subprocess.run([ADB, "-s", DEV] + args, capture_output=True,
                           timeout=timeout)
        return r.stdout.decode("utf-8", "replace")
    except Exception as e:
        return "ERR %r" % e


def cpu_of(pattern):
    txt = adb(["shell", "dumpsys", "cpuinfo"])
    pct = 0.0
    for line in txt.splitlines():
        if pattern in line:
            m = re.search(r"([\d.]+)%", line)
            if m:
                pct += float(m.group(1))
    return pct


def rotations():
    n = 0
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                if ("泵流满" in line or "泵流静默" in line
                        or "泵流提前EOF" in line):
                    n += 1
    except OSError:
        pass
    return n


def main():
    base_rot = rotations()
    t0 = time.time()
    last_rot = base_rot
    print("%6s %9s %10s %8s %9s %9s %s" % (
        "t(s)", "time-pos", "cache(s)", "本条轮换", "代理CPU%", "影视仓CPU%",
        "进程"))
    while time.time() - t0 < TOTAL:
        s = props(["time-pos", "demuxer-cache-time", "core-idle"])
        rot = rotations()
        pct_proxy = cpu_of("new_go_proxy_wex")
        pct_app = cpu_of("com.huawei.himovceie")
        alive = adb(["shell", "pidof com.huawei.himovceie"]).strip()
        print("%6.0f %9s %10s %8d %9.1f %9.1f %s" % (
            time.time() - t0, s.get("time-pos"), s.get("demuxer-cache-time"),
            rot - last_rot, pct_proxy, pct_app,
            "在" if alive else "**已死**"))
        last_rot = rot
        time.sleep(STEP)
    print("总轮换次数: %d（观察 %.0f 秒）" % (rotations() - base_rot, TOTAL))
    kills = adb(["shell", "logcat", "-d", "-v", "time"])
    hits = [l for l in kills.splitlines()
            if "excessive cpu" in l or "Killing PhantomProcess" in l]
    print("本轮 logcat 中的杀进程记录: %d" % len(hits))
    for h in hits[-3:]:
        print("   ", h.strip()[:160])


if __name__ == "__main__":
    main()
