# -*- coding: utf-8 -*-
"""走 mpv IPC：导出当前解码帧到 PNG，并打印播放进度。用法: python grab_frame.py"""
import json
import time

PIPE = r"\\.\pipe\playbridge_mpv"
OUT = r"D:\PlayBridge\PlayBridgeHost\mpv_frame.png"


def main():
    f = open(PIPE, "r+b", buffering=0)
    cmds = [
        ["screenshot-to-file", OUT, "video"],
        ["get_property", "time-pos"],
        ["get_property", "width"],
        ["get_property", "height"],
        ["get_property", "video-codec"],
        ["get_property", "estimated-vf-fps"],
    ]
    for i, c in enumerate(cmds, 1):
        f.write((json.dumps({"command": c, "request_id": i}) + "\n").encode())
    f.flush()
    got = {}
    t0 = time.time()
    while len(got) < len(cmds) and time.time() - t0 < 6:
        line = f.readline()
        if not line:
            break
        try:
            m = json.loads(line)
        except ValueError:
            continue
        rid = m.get("request_id")
        if rid:
            got[rid] = m
    f.close()

    r = got.get(1) or {}
    print("截图 ->", r.get("error"), r.get("data"))
    for i, name in [(2, "time-pos"), (3, "width"), (4, "height"),
                    (5, "video-codec"), (6, "fps")]:
        m = got.get(i) or {}
        print("  %-12s %s" % (name, m.get("data", m.get("error"))))


if __name__ == "__main__":
    main()
