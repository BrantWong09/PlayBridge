# -*- coding: utf-8 -*-
"""mpv_gate — 跳转（seek）后的预缓冲闸门。

起播那一次不需要这里管：mpv 自己的 --demuxer-cache-wait=yes 就会等
--cache-secs 攒够才开播（已实测）。这里只管"用户点到前面或后面"：
收到 mpv 的 seek 事件就把播放闸住，等前向缓存重新攒够 PRE 秒再放行。

实测要点：暂停并不会阻止 demuxer 预读——跳转后立刻 pause=yes，缓存照样
从 10s 涨到 30.4s 并停在那儿；所以"暂停 + 等缓存"是可行的。
"""
import json
import threading
import time

PIPE = r"\\.\pipe\playbridge_mpv"
PRE = 30.0            # seek 后要求的前向缓存秒数（与 Host 的 --cache-secs 对齐）
MAX_WAIT = 60.0       # 攒不够也先放，避免把播放永久卡住
POLL = 0.25
CONNECT_TRIES = 40    # 每次 0.5s，等 mpv 把管道建起来
CONNECT_GAP = 0.5


class Gate(threading.Thread):

    def __init__(self, log):
        super().__init__(daemon=True)
        self.log = log
        self.rid = 0

    # ---------- IPC ----------
    def _connect(self):
        for _ in range(CONNECT_TRIES):
            try:
                return open(PIPE, "r+b", buffering=0)
            except OSError:
                time.sleep(CONNECT_GAP)
        return None

    def _cmd(self, f, cmd):
        self.rid += 1
        rid = self.rid
        f.write((json.dumps({"command": cmd, "request_id": rid})
                 + "\n").encode())
        while True:
            line = f.readline()
            if not line:
                raise EOFError("mpv IPC 断开")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("request_id") == rid:
                return msg

    def _prop(self, f, name):
        m = self._cmd(f, ["get_property", name])
        if m.get("error") == "success":
            return m.get("data")
        return None

    def _ahead(self, f):
        """前向缓存秒数 = demuxer-cache-time（缓存末端时间戳）- time-pos。"""
        cache = self._prop(f, "demuxer-cache-time")
        pos = self._prop(f, "time-pos")
        if cache is None or pos is None:
            return None
        return cache - pos

    def _show(self, f):
        """把窗口从最小化里拉出来：实测起播/跳转后它常被最小化，用户看不到画面。"""
        try:
            self._cmd(f, ["set_property", "window-minimized", False])
        except Exception:
            pass

    def _keep_visible(self, seconds=90.0):
        """起播这段最容易出现"窗口被最小化"，期间独立连接盯一下。"""
        try:
            g = open(PIPE, "r+b", buffering=0)
        except OSError:
            return
        rid = 900000
        t0 = time.time()
        while time.time() - t0 < seconds:
            try:
                rid += 1
                g.write((json.dumps({
                    "command": ["get_property", "window-minimized"],
                    "request_id": rid}) + "\n").encode())
                data = None
                while True:
                    line = g.readline()
                    if not line:
                        return
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if m.get("request_id") == rid:
                        if m.get("error") == "success":
                            data = m.get("data")
                        break
                if data:
                    rid += 1
                    g.write((json.dumps({
                        "command": ["set_property", "window-minimized",
                                    False],
                        "request_id": rid}) + "\n").encode())
                    while True:
                        line = g.readline()
                        if not line:
                            return
                        try:
                            m = json.loads(line)
                        except ValueError:
                            continue
                        if m.get("request_id") == rid:
                            break
                    self.log("gate: 窗口被最小化了 → 已拉回前台")
            except (OSError, ValueError):
                return
            time.sleep(2)
        g.close()

    # ---------- 主循环 ----------
    def run(self):
        f = self._connect()
        if f is None:
            self.log("gate: 连不上 mpv IPC，seek 预缓冲未生效")
            return
        self.log("gate: 已挂上（seek 后重新攒 %.0fs）" % PRE)
        self._show(f)
        threading.Thread(target=self._keep_visible, daemon=True).start()
        try:
            while True:
                line = f.readline()          # 阻塞等事件
                if not line:
                    raise EOFError("mpv IPC 断开")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("event") != "seek":
                    continue
                # 跳转了 → 闸住再攒
                was_paused = bool(self._prop(f, "pause"))
                if not was_paused:
                    self._cmd(f, ["set_property", "pause", True])
                t0 = time.time()
                ahead = None
                while True:
                    ahead = self._ahead(f)
                    if ahead is not None and ahead >= PRE:
                        break
                    if time.time() - t0 >= MAX_WAIT:
                        self.log("gate: seek 后等 %.0fs 只攒到 %s，先放行"
                                 % (MAX_WAIT, ("%.1fs" % ahead)
                                    if ahead is not None else "无"))
                        break
                    time.sleep(POLL)
                if not was_paused:
                    self._cmd(f, ["set_property", "pause", False])
                self._show(f)
                self.log("gate: seek 后缓存 %.1fs → 继续播放"
                         % (ahead if ahead is not None else -1))
        except (EOFError, OSError, ValueError) as e:
            self.log("gate: 停止 (%r)" % e)
            try:
                self._cmd(f, ["set_property", "pause", False])
            except Exception:
                pass
        finally:
            try:
                f.close()
            except Exception:
                pass


_lock = threading.Lock()


def start(log):
    """每次拉起 mpv 之后调一次。"""
    with _lock:
        Gate(log).start()
