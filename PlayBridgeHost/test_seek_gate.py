# -*- coding: utf-8 -*-
"""测两件事：
1) 跳转后 mpv 自己会不会重新攒缓存（--cache-pause 的默认行为）
2) 暂停状态下 mpv 到底还读不读流（决定闸门方案是否可行）
用法: python test_seek_gate.py [跳到秒] [等起播秒]
"""
import json
import sys
import time

PIPE = r"\\.\pipe\playbridge_mpv"
SEEK_TO = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
WAIT_PLAY = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0


def cmd(f, c, rid):
    f.write((json.dumps({"command": c, "request_id": rid}) + "\n").encode())
    while True:
        line = f.readline()
        if not line:
            raise EOFError("IPC 断开")
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("request_id") == rid:
            return m


def sample(f, rid):
    out = {}
    for p in ["time-pos", "demuxer-cache-time", "pause", "core-idle"]:
        rid += 1
        m = cmd(f, ["get_property", p], rid)
        out[p] = m.get("data") if m.get("error") == "success" else None
    return out, rid


def main():
    f = open(PIPE, "r+b", buffering=0)
    rid = 100
    t0 = time.time()

    # 1) 等起播
    print("--- 等起播（最长 %.0fs）---" % WAIT_PLAY)
    while time.time() - t0 < WAIT_PLAY:
        s, rid = sample(f, rid)
        print("[%5.1fs] cache=%-10s time-pos=%-8s pause=%s idle=%s" % (
            time.time() - t0, s["demuxer-cache-time"], s["time-pos"],
            s["pause"], s["core-idle"]))
        if s["time-pos"] and s["time-pos"] > 1.0:
            break
        time.sleep(3)

    # 2) 跳转并立刻闸住，看暂停状态下缓存还涨不涨
    rid += 1
    cmd(f, ["set_property", "pause", True], rid)
    rid += 1
    m = cmd(f, ["seek", SEEK_TO, "absolute+exact"], rid)
    print("--- seek → %.0fs (%s) 并立刻暂停，看缓存是否继续攒 ---"
          % (SEEK_TO, m.get("error")))
    t1 = time.time()
    while time.time() - t1 < 30:
        s, rid = sample(f, rid)
        ahead = None
        if s["demuxer-cache-time"] is not None and s["time-pos"] is not None:
            ahead = s["demuxer-cache-time"] - s["time-pos"]
        print("[%5.1fs] cache=%-10s time-pos=%-8s 领先=%-8s pause=%s idle=%s" % (
            time.time() - t1, s["demuxer-cache-time"], s["time-pos"],
            ("%.1fs" % ahead) if ahead is not None else None,
            s["pause"], s["core-idle"]))
        time.sleep(3)

    rid += 1
    cmd(f, ["set_property", "pause", False], rid)
    print("已解除暂停")
    f.close()


if __name__ == "__main__":
    main()
