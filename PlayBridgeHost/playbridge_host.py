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
# 影视仓所在的模拟器 adb 地址：启动时自动探测。MuMu 重启后 adb 端口会变
# （实测同一实例在 16384/7555/5555 三个 transport 上，旧的 16416 直接拒连），
# 写死会让 adb forward 静默失败、中继拿不到上游。要强制指定就用环境变量。
DEVICE = ""
MUMU_PORTS = (16384, 7555, 5555, 62001, 62025, 16416)   # 无设备时依次 connect 尝试
PROXY_PORT = 8096                 # 模拟器内影视仓代理端口
FORWARD_PORT = 18096              # Windows 本地隧道端口
WUKONG_PKG = "com.huawei.himovceie"                              # 影视仓包名
WUKONG_ACT = "com.github.tvbox.osc.ui.activity.HomeActivity"     # 启动 Activity

mpv_proc = None


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _adb_devices():
    """adb devices 里 state=device 的序列号列表。"""
    try:
        r = subprocess.run([ADB, "devices"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    serials = []
    for line in (r.stdout or b"").decode("utf-8", "replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _shell(serial, *cmd, timeout=15):
    """跑一条 adb shell，返回 stdout 文本；失败返回空串。"""
    try:
        r = subprocess.run([ADB, "-s", serial, "shell"] + list(cmd),
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if r.returncode != 0:
        return ""
    return (r.stdout or b"").decode("utf-8", "replace")


def _android_id(serial):
    return _shell(serial, "settings", "get", "secure", "android_id").strip()


def _has_wukong(serial):
    """该设备上是否装了影视仓——多设备时用它挑对实例。"""
    return ("package:%s" % WUKONG_PKG) in _shell(
        serial, "pm", "list", "packages", WUKONG_PKG)


def detect_device():
    """探测影视仓所在的模拟器 adb 地址，返回 (serial, 依据)。

    顺序：环境变量 PLAYBRIDGE_DEVICE > 装了影视仓的设备 > 第一个可用设备。
    MuMu 会给同一实例暴露多个 transport（16384/7555/5555），按 android_id
    去重，避免把同一个实例当成多台设备反复切换。
    """
    forced = os.environ.get("PLAYBRIDGE_DEVICE", "").strip()
    if forced:
        return forced, "环境变量 PLAYBRIDGE_DEVICE"

    serials = _adb_devices()
    if not serials:
        for port in MUMU_PORTS:            # 模拟器起着但 adb 没连上
            try:
                subprocess.run([ADB, "connect", "127.0.0.1:%d" % port],
                               capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        serials = _adb_devices()
    if not serials:
        return "", "未发现 adb 设备"

    unique, seen = [], set()
    for s in serials:
        key = _android_id(s) or s
        if key not in seen:
            seen.add(key)
            unique.append(s)
    for s in unique:
        if _has_wukong(s):
            return s, "装了 %s" % WUKONG_PKG
    return unique[0], "唯一可用设备"


def current_device():
    """DEVICE 为空时（启动时模拟器还没起）现场重探一次，用于自愈。"""
    global DEVICE
    if not DEVICE:
        DEVICE, why = detect_device()
        if DEVICE:
            log("设备: %s (%s)" % (DEVICE, why))
        else:
            log("设备: 无可用 adb 设备 (%s)" % why)
    return DEVICE


def ensure_forward():
    """确保设备已连上 adb 并建立 forward tcp:18096 -> 模拟器 tcp:8096（幂等）。

    以前只做 forward、不 connect，也不看返回码：一旦 adb server 丢了设备
    （MuMu 重启/adb 重启），forward 静默失败，中继就一直 ConnectionRefused。
    这里补上 connect 并把失败打进日志，配合 ring_relay 的重连钩子可自愈。
    设备地址是启动时探测的；若 adb 说设备没了（端口又变了），清掉缓存让
    下一次重新探测，而不是一直对着死地址重试。
    """
    global DEVICE
    dev = current_device()
    if not dev:
        return
    try:
        if ":" in dev:                      # 只有 host:port 形式才能 connect
            subprocess.run([ADB, "connect", dev],
                           capture_output=True, timeout=10)
        r = subprocess.run(
            [ADB, "-s", dev, "forward", "tcp:%d" % FORWARD_PORT,
             "tcp:%d" % PROXY_PORT],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()
            log("adb forward 失败: %s" % (err or "exit %d" % r.returncode))
            if "not found" in err or "offline" in err:
                DEVICE = ""                 # 地址失效 → 下次重新探测
        else:
            log("adb forward 就绪: tcp:%d -> %s tcp:%d"
                % (FORWARD_PORT, dev, PROXY_PORT))
    except (OSError, subprocess.TimeoutExpired) as e:
        log("adb forward 异常: %s" % e)


def _wukong_alive(dev):
    return bool(_shell(dev, "pidof", WUKONG_PKG).strip())


def restart_proxy():
    """上游代理挂死时重启影视仓，重建 new_go_proxy_wex。

    实测代理被喂挂后不会再应答，只有重启进程才能恢复；重启后同一条
    百度直链仍可经新代理继续播放，播放器不用换源。

    两个坑（都实测踩过）：
    1. 必须看 adb 的返回码，以前不看返回码、无条件打「已重启」，设备地址
       失配时每轮都在空转却报健康，代理就一直挂着没人救。
    2. `am start -n` 返回 0 也可能什么都没启动：外部播放把 PlayBridge 的
       MainActivity 压进了影视仓的 task，系统会把 intent 投递给同 task 栈顶
       的那个 Activity（回 "delivered to currently running top-most instance"），
       影视仓进程不会被拉起。加 -S / NEW_TASK 都无效，只有 monkey 走
       LAUNCHER intent 可靠。所以这里以 pidof 为准，起不来就换 monkey。
    """
    dev = current_device()
    if not dev:
        log("ring: 重启影视仓失败: 无可用 adb 设备")
        return
    try:
        subprocess.run([ADB, "-s", dev, "shell", "am", "force-stop",
                        WUKONG_PKG], capture_output=True, timeout=15)
        time.sleep(1)
        r2 = subprocess.run([ADB, "-s", dev, "shell", "am", "start", "-n",
                             "%s/%s" % (WUKONG_PKG, WUKONG_ACT)],
                            capture_output=True, timeout=15)
        for _ in range(5):                      # 冷启动要等脱壳，给 5s
            if _wukong_alive(dev):
                break
            time.sleep(1)
        if not _wukong_alive(dev):
            err = (r2.stderr or b"").decode("utf-8", "replace").strip()
            log("ring: am start 未拉起影视仓%s，改用 monkey"
                % (": %s" % err if err else ""))
            subprocess.run([ADB, "-s", dev, "shell", "monkey", "-p",
                            WUKONG_PKG, "-c",
                            "android.intent.category.LAUNCHER", "1"],
                           capture_output=True, timeout=20)
            for _ in range(10):
                if _wukong_alive(dev):
                    break
                time.sleep(1)
        if _wukong_alive(dev):
            log("ring: 已重启影视仓以恢复上游代理")
        else:
            log("ring: 重启影视仓失败: 进程未起来（am start / monkey 都无效）")
    except (OSError, subprocess.TimeoutExpired) as e:
        log("ring: 重启影视仓失败: %s" % e)


def rewrite_url(url):
    """数据面适配：把指向模拟器代理的 URL 改写为走本机隧道。
    控制面（Android 端）不感知，改写只发生在 Host。"""
    return url.replace("127.0.0.1:%d" % PROXY_PORT,
                       "127.0.0.1:%d" % FORWARD_PORT)


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
    global DEVICE
    host_ip = socket.gethostbyname(socket.gethostname())
    DEVICE, why = detect_device()
    if DEVICE:
        log("设备: %s (%s)" % (DEVICE, why))
    else:
        log("设备: 未探测到 adb 设备 (%s)；播放请求时会重探，"
            "也可用 PLAYBRIDGE_DEVICE 指定" % why)
    # 上游持续不可用时重跑 adb forward（隧道也可能掉），让泵自动恢复
    ring_relay.set_reconnect_hook(ensure_forward)
    # 代理被喂挂（持续不可用）时重启影视仓，重建代理进程
    ring_relay.set_proxy_restart_hook(restart_proxy)
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
