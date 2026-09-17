# -*- coding: utf-8 -*-
"""
PlayBridge Host (Windows, MVP)
- 监听 0.0.0.0:16888（控制通道，只收几 KB 的 JSON，不碰视频数据）
- GET  /ping  → 连通性测试
- POST /play  → 收到播放请求，打印并记录到 playbridge.log
以后接 mpv/PotPlayer 只需要改 handle_play()。
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ring_relay
import mpv_gate

PORT = 16888
BASE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE, "playbridge.log")
MPV_LOG = os.path.join(BASE, "mpv.log")

MPV = r"C:\Users\Administrator\AppData\Roaming\com.geon.quantumtv\mpv\mpv.exe"
ADB = r"C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe"
DEVICE = "127.0.0.1:16416"        # 影视仓所在的 MuMu 实例（MuMuManager 查询所得）
PROXY_PORT = 8096                 # 模拟器内影视仓代理端口
FORWARD_PORT = 18096              # Windows 本地隧道端口

mpv_proc = None


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def ensure_forward():
    """确保 adb forward tcp:18096 -> 模拟器 tcp:8096 存在（幂等）"""
    try:
        subprocess.run(
            [ADB, "-s", DEVICE, "forward", "tcp:%d" % FORWARD_PORT,
             "tcp:%d" % PROXY_PORT],
            capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        log("adb forward 失败: %s" % e)


def rewrite_url(url):
    """数据面适配：把指向模拟器代理的 URL 改写为走本机隧道。
    控制面（Android 端）不感知，改写只发生在 Host。"""
    return url.replace("127.0.0.1:%d" % PROXY_PORT,
                       "127.0.0.1:%d" % FORWARD_PORT)


def bring_mpv_to_front(pid, tries=6):
    """窗口可能创建稍晚于进程启动，轮询找它的顶级窗口并置前"""
    import ctypes
    user32 = ctypes.windll.user32
    seen_pid = ctypes.c_ulong()

    def enum_proc():
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(hwnd, lparam):
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(seen_pid))
            if seen_pid.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True
        user32.EnumWindows(cb, 0)
        return found

    for _ in range(tries):
        for hwnd in enum_proc():
            user32.ShowWindow(hwnd, 9)    # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.5)
    return False


def capture_raw(play_url, seconds=25):
    """诊断用：单发 Range GET，读取至多 512KB 并记录前 512 字节 hex"""
    import http.client
    import urllib.parse
    p = urllib.parse.urlsplit(play_url)
    path = p.path + ("?" + p.query if p.query else "")
    try:
        conn = http.client.HTTPConnection(p.hostname, p.port, timeout=seconds)
        conn.request("GET", path, headers={
            "Range": "bytes=0-",
            "User-Agent": "nPlayer",
        })
        resp = conn.getresponse()
        log("  [capture] status=%s type=%s length=%s" % (
            resp.status, resp.getheader("Content-Type"),
            resp.getheader("Content-Length")))
        buf = b""
        import time as _t
        t0 = _t.time()
        while len(buf) < 512 * 1024 and _t.time() - t0 < seconds:
            try:
                chunk = resp.read(65536)
            except Exception as e:
                log("  [capture] 读中断: %r" % e)
                break
            if not chunk:
                break
            buf += chunk
        log("  [capture] got=%d bytes in %.1fs" % (len(buf), _t.time() - t0))
        if buf:
            log("  [capture] head-hex: %s" % buf[:512].hex())
            printable = bytes(c if 32 <= c < 127 else 46 for c in buf[:200])
            log("  [capture] head-txt: %s" % printable.decode())
        conn.close()
    except Exception as e:
        log("  [capture] 失败: %r" % e)


def handle_play(data):
    """收到播放请求 → 拉起 mpv 播放"""
    global mpv_proc

    url = data.get("url") or ""
    log("=" * 60)
    log("PLAY REQUEST")
    log("  version  = %s" % data.get("version"))
    log("  action   = %s" % data.get("action"))
    log("  title    = %s" % data.get("title"))
    log("  position = %s" % data.get("position"))
    if data.get("extras"):
        log("  extras   = %s" % json.dumps(data["extras"], ensure_ascii=False))
    log("  URL      = %s" % url[:120] + ("..." if len(url) > 120 else ""))
    # 完整 URL 另存一份，供离线重放调试（会随时间过期）
    try:
        with open(os.path.join(BASE, "last_play.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass

    if not url:
        log("  无 URL，忽略")
        log("=" * 60)
        return

    if "127.0.0.1:%d" % PROXY_PORT in url:
        ensure_forward()
        play_url = rewrite_url(url)
        log("  代理型源 → 走隧道 %s" % ("127.0.0.1:%d" % FORWARD_PORT))
    else:
        play_url = url

    if os.path.exists(os.path.join(BASE, "debug_capture")):
        log("  [调试模式] 原始捕获，不启动 mpv")
        threading.Thread(target=capture_raw, args=(play_url,), daemon=True).start()
        return

    is_proxy = "127.0.0.1:%d" % FORWARD_PORT in play_url
    if is_proxy and ring_relay.USE_RELAY:
        ring_relay.ensure_started(port=ring_relay.RELAY_PORT, log=log)
        log("  中继连接上游...")
        ring = ring_relay.set_target(play_url, log)
        if ring is None:
            log("  中继建立失败，mpv 直连隧道")
            mpv_url = play_url
            is_proxy = False
        else:
            mpv_url = "http://127.0.0.1:%d/stream" % ring_relay.RELAY_PORT
            log("  经单流环形中继 :%d" % ring_relay.RELAY_PORT)
    else:
        mpv_url = play_url

    args = [
        MPV,
        "--force-window=yes",
        "--autofit=80%",
        "--ontop=yes",                    # 置顶，避免被 MuMu 窗口盖住
        "--log-file=" + os.path.join(BASE, "mpv.log"),
        "--msg-level=curl=v,stream=v",
        "--input-ipc-server=\\\\.\\pipe\\playbridge_mpv",
        "--title=PlayBridge",
        "--no-ytdl",                      # 禁止 ytdl-hook 把 URL 当网页重新解析（曾导致自动跳到别的片段）
        "--no-resume-playback",
        # 起播不要等太久：8s 缓冲即开播，后续由中继环形缓冲兜底
        "--demuxer-readahead-secs=8",
        "--demuxer-max-bytes=256MiB",
        "--demuxer-max-back-bytes=64MiB",
        "--cache=yes",
        "--cache-secs=30",                # 预缓冲目标：攒够 30s 再开播
        "--demuxer-cache-wait=yes",       # 起播前先等缓存达标（实测有效）
    ]
    if is_proxy and not ring_relay.USE_RELAY:
        # kaiser 代理按 UA 白名单区别响应：必须伪装成影视仓自家 ExoPlayer
        args += [
            "--user-agent=com.android.chrome/131.0.6778.200 (Linux;Android 10) AndroidXMedia3/1.5.1",
        ]

    if mpv_proc and mpv_proc.poll() is None:
        mpv_proc.terminate()

    args.append(mpv_url)
    try:
        mpv_proc = subprocess.Popen(args)
        log("  mpv 已拉起 (pid=%d)" % mpv_proc.pid)
        mpv_gate.start(log)               # seek 后重新攒够预缓冲再放行
        bring_mpv_to_front(mpv_proc.pid)

        def watch(proc=mpv_proc, u=play_url):
            code = proc.wait()
            log("  mpv 退出 code=%s  (url=%s)" % (code, u[:100]))
        threading.Thread(target=watch, daemon=True).start()
    except OSError as e:
        log("  启动 mpv 失败: %s" % e)
    log("=" * 60)


class Handler(BaseHTTPRequestHandler):

    def _reply(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/ping"):
            self._reply(200, {"ok": True, "service": "playbridge-host", "version": 1})
        else:
            self._reply(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/play"):
            self._reply(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            log("POST /play 解析失败: %s" % e)
            self._reply(400, {"ok": False, "error": "bad json"})
            return
        handle_play(data)
        self._reply(200, {"ok": True})

    def log_message(self, fmt, *args):
        pass  # 用自己的 log，不用默认的 stderr 噪音


def main():
    host_ip = socket.gethostbyname(socket.gethostname())
    log("PlayBridge Host 启动: 端口 %d (本机IP %s)" % (PORT, host_ip))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError as e:
        log("端口 %d 被占用: %s" % (PORT, e))
        sys.exit(1)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("已停止")


if __name__ == "__main__":
    main()
