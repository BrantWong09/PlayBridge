# -*- coding: utf-8 -*-
"""单独验证：mpv 在 --pause=yes 状态下会不会继续预读（demuxer-cache-time 是否增长）。
用独立 IPC 管道，避免和 Host 的 mpv_gate 互相干扰。
用法: python test_pause_prefetch.py [观察秒数]
"""
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
MPV = r"C:\Users\Administrator\AppData\Roaming\com.geon.quantumtv\mpv\mpv.exe"
PIPE = r"\\.\pipe\pb_test"
TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 60
URL = open("probe_url.txt", encoding="utf-8").read().strip()
URL = URL.replace("127.0.0.1:18096", "127.0.0.1:18095").replace(
    "/kaiser?", "/stream#")


def main():
    url = "http://127.0.0.1:18095/stream"
    args = [
        MPV, "--pause=yes", "--force-window=no", "--vo=null", "--ao=null",
        "--no-ytdl", "--no-resume-playback",
        "--log-file=" + os.path.join(BASE, "mpv_pause_test.log"),
        "--msg-level=cplayer=v",
        "--input-ipc-server=" + PIPE,
        "--demuxer-max-bytes=256MiB", "--cache=yes", "--cache-secs=60",
        url,
    ]
    p = subprocess.Popen(args)
    print("mpv 已拉起 pid=%d (暂停状态) url=%s" % (p.pid, url))

    f = None
    for _ in range(30):
        try:
            f = open(PIPE, "r+b", buffering=0)
            break
        except OSError:
            time.sleep(0.5)
    if f is None:
        print("连不上 IPC 管道")
        p.terminate()
        return

    props = ["pause", "demuxer-cache-time", "time-pos", "core-idle",
             "demuxer-cache-state"]
    t0 = time.time()
    rid = 0
    while time.time() - t0 < TOTAL:
        res = {}
        for i, pr in enumerate(props):
            rid += 1
            f.write((json.dumps({"command": ["get_property", pr],
                                 "request_id": rid}) + "\n").encode())
            want = rid
            while True:
                line = f.readline()
                if not line:
                    print("IPC 断开")
                    p.terminate()
                    return
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("request_id") == want:
                    res[pr] = m.get("data") if m.get("error") == "success" \
                        else None
                    break
        cs = res.get("demuxer-cache-state") or {}
        fwd = cs.get("fw-bytes") if isinstance(cs, dict) else None
        print("[%5.1fs] pause=%s cache(s)=%s time-pos=%s idle=%s fw-bytes=%s"
              % (time.time() - t0, res.get("pause"),
                 res.get("demuxer-cache-time"), res.get("time-pos"),
                 res.get("core-idle"), fwd))
        if time.time() - t0 > 12 and res.get("pause") is False:
            print("     ^^ 已经自行解除暂停（不是我们要的）")
        time.sleep(3)

    print("观察结束，终止 mpv")
    p.terminate()


if __name__ == "__main__":
    main()
