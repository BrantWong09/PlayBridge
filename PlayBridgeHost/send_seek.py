# -*- coding: utf-8 -*-
"""向 Host 拉起的 mpv 发一个绝对跳转。用法: python send_seek.py <秒>"""
import json
import sys
import time

PIPE = r"\\.\pipe\playbridge_mpv"
TARGET = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0


def main():
    f = open(PIPE, "r+b", buffering=0)
    f.write((json.dumps({"command": ["seek", TARGET, "absolute+exact"],
                         "request_id": 1}) + "\n").encode())
    while True:
        line = f.readline()
        if not line:
            print("IPC 断开")
            break
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("request_id") == 1:
            print("seek → %.0fs : %s" % (TARGET, m.get("error")))
            break
    f.close()


if __name__ == "__main__":
    main()
