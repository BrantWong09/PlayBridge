# -*- coding: utf-8 -*-
"""按 Host 完全相同的参数手工拉 mpv，连中继的 /stream（用于验证热环能否直接播）。
用法: python launch_mpv_manual.py
"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
MPV = r"C:\Users\Administrator\AppData\Roaming\com.geon.quantumtv\mpv\mpv.exe"

args = [
    MPV,
    "--force-window=yes",
    "--autofit=80%",
    "--ontop=yes",
    "--log-file=" + os.path.join(BASE, "mpv_manual.log"),
    "--msg-level=curl=v,stream=v",
    "--input-ipc-server=\\\\.\\pipe\\playbridge_mpv",
    "--title=PlayBridge",
    "--no-ytdl",
    "--no-resume-playback",
    "--demuxer-readahead-secs=8",
    "--demuxer-max-bytes=256MiB",
    "--demuxer-max-back-bytes=64MiB",
    "--cache=yes",
    "--cache-secs=60",
    "http://127.0.0.1:18095/stream",
]
p = subprocess.Popen(args)
print("mpv 已拉起 pid=%d" % p.pid)
