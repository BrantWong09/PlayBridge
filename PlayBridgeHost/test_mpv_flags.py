# -*- coding: utf-8 -*-
"""通用测试台：用指定 mpv 参数连中继，打印缓存/播放时间线，用来验证
--cache-pause-initial / --cache-secs 等参数到底等多少缓存才开播。

用法: python test_mpv_flags.py "<额外参数>" [观察秒数]
例:   python test_mpv_flags.py "--cache-pause-initial=yes --cache-secs=30" 90
"""
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
MPV = r"C:\Users\Administrator\AppData\Roaming\com.geon.quantumtv\mpv\mpv.exe"
PIPE = r"\\.\pipe\pb_test"
URL = "http://127.0.0.1:18095/stream"


def main():
    extra = sys.argv[1].split() if len(sys.argv) > 1 else []
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    seek_at = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    seek_to = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

    args = [MPV, "--force-window=no", "--vo=null", "--ao=null",
            "--no-ytdl", "--no-resume-playback",
            "--log-file=" + os.path.join(BASE, "mpv_flags_test.log"),
            "--msg-level=cplayer=v,stream=v",
            "--input-ipc-server=" + PIPE,
            "--demuxer-max-bytes=256MiB",
            "--demuxer-readahead-secs=8"] + extra + [URL]
    p = subprocess.Popen(args)
    print("mpv pid=%d 参数: %s" % (p.pid, " ".join(extra) or "(无)"))

    f = None
    for _ in range(30):
        try:
            f = open(PIPE, "r+b", buffering=0)
            break
        except OSError:
            time.sleep(0.5)
    if f is None:
        print("连不上 IPC")
        p.terminate()
        return

    props = ["pause", "demuxer-cache-time", "time-pos", "core-idle",
             "eof-reached"]
    t0 = time.time()
    rid = 0
    first_playing = None
    seek_done = False
    while time.time() - t0 < total:
        el = time.time() - t0
        if seek_at and not seek_done and el >= seek_at:
            seek_done = True
            rid += 1
            cmd = {"command": ["seek", seek_to, "absolute+exact"],
                   "request_id": rid}
            f.write((json.dumps(cmd) + "\n").encode())
            while True:
                line = f.readline()
                if not line:
                    break
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("request_id") == rid:
                    print(">>> [%5.1fs] 发起 seek → %.0fs (结果 %s)"
                          % (el, seek_to, m.get("error")))
                    break
        res = {}
        for pr in props:
            rid += 1
            f.write((json.dumps({"command": ["get_property", pr],
                                 "request_id": rid}) + "\n").encode())
            while True:
                line = f.readline()
                if not line:
                    print("IPC 断开（mpv 退出?）")
                    p.terminate()
                    return
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("request_id") == rid:
                    res[pr] = m.get("data") if m.get("error") == "success" \
                        else None
                    break
        tp = res.get("time-pos")
        if tp and first_playing is None and tp > 0.5:
            first_playing = el
        print("[%5.1fs] cache(s)=%-11s time-pos=%-8s pause=%-5s idle=%-5s eof=%s"
              % (el, res.get("demuxer-cache-time"), tp,
                 res.get("pause"), res.get("core-idle"),
                 res.get("eof-reached")))
        if res.get("eof-reached"):
            print("    ^^ EOF reached")
            break
        time.sleep(3)

    if first_playing is not None:
        print("首次出现 time-pos>0.5 的时刻: %.1fs" % first_playing)
    else:
        print("观察期内没有开始播放")
    print("终止 mpv")
    p.terminate()


if __name__ == "__main__":
    main()
