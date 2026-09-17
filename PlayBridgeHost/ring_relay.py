# -*- coding: utf-8 -*-
"""ring_relay — PlayBridge 本地中继（数据面，v5.1：轮换泵 + 绝对偏移字节环）。

代理实测行为（抓包 + 隧道测试）：
- 每条长流喂 ~35-150MB 后停止吐字节但保持连接 → 必须定期/按静默重开
- 任意偏移的 `bytes=P-` 都接受（ExoPlayer 就是连开多条不同起点）
- 频繁重开（每 1MB）会被掐死 → 重开要有节奏

设计：
- 泵线程：一条 `bytes=read_pos-` 长流顺序搬进绝对偏移字节环；
  流静默(2.5s)或累计喂满 ROTATE(64MB) → 主动从 read_pos 重开下一条（轮换）。
- 环：永不超过 mpv 消费位置 + RING_MAX，也不早于 mpv 消费位置 - LOOKBACK
  丢弃 → 泵永不越过 mpv 要读的位置。
- 旁路：mpv 跳读/回看被丢弃区 → 单发 `bytes=start-` 拉够即断（ExoPlayer 同款），
  不动主泵。
"""
import http.client
import re
import threading
import time
import urllib.parse

RELAY_PORT = 18095
USE_RELAY = True
CHUNK = 1 << 20
READ_BLOCK = 1 << 16
SILENT = 8.0           # 泵流静默判死（秒）——代理缓存满会短暂停顿，别误杀
ROTATE = 64 << 20      # 每条流最多喂 64MB 就轮换重开
MAX_STRIKES = 4        # 连续建流失败次数上限，超过则泵死亡（不再风暴）
RING_MAX = 200 << 20   # 超前 mpv 最多缓存量
LOOKBACK = 24 << 20    # 滞后 mpv 保留量
BYPASS_CHUNK = 32 << 20
UA = "com.android.chrome/131.0.6778.200 (Linux;Android 10) AndroidXMedia3/1.5.1"

_current = None
_current_lock = threading.Lock()


class UpstreamError(Exception):
    pass


class Pump:

    def __init__(self, target_url, log=lambda m: None):
        p = urllib.parse.urlsplit(target_url)
        self.host, self.port = p.hostname, p.port
        self.path = p.path + ("?" + p.query if p.query else "")
        self.log = log
        self.lock = threading.Condition()
        self.buf = bytearray()       # 绝对偏移 [base, read_pos)
        self.base = 0
        self.read_pos = 0
        self.served = 0              # mpv 已消费位置（由 handler 更新）
        self.total = None
        self.ctype = "video/x-matroska"
        self.eof = False
        self.dead = False
        self.disposed = False
        self.started = False
        self.strikes = 0
        self.gen = 0                 # 重定代次：变了就让当前流作废

    # ---------- 泵 ----------
    def start(self):
        if not self.started:
            self.started = True
            threading.Thread(target=self._pump_loop, daemon=True).start()

    def retarget(self, pos):
        """消费者跳到新位置：把泵重定过去，让环跟着新位置长。

        不这么做的话，跳转后的整段播放只能靠 _bypass 一条条并发拉上游流，
        代理扛不住（实测几个请求就把代理打到不应答）。
        """
        with self.lock:
            self.gen += 1
            self.base = pos
            self.read_pos = pos
            self.served = pos
            del self.buf[:]
            self.lock.notify_all()
        self.log("ring: 泵重定到 %d" % pos)

    def _hard_close(self, conn):
        """带未读数据的 HTTPConnection.close() 可能阻塞甚至不真关；
        直接掐 socket（linger=0 → 发 RST，代理立即释放会话槽）。"""
        try:
            sock = getattr(conn, "sock", None)
            if sock is not None:
                import socket as _s
                try:
                    sock.setsockopt(_s.SOL_SOCKET, _s.SO_LINGER,
                                    _s.struct.pack("ii", 1, 0))
                except Exception:
                    pass
                sock.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    def _pump_loop(self):
        try:
            conn = http.client.HTTPConnection(self.host, self.port,
                                              timeout=SILENT)
            with self.lock:
                start = self.read_pos
                gen = self.gen
                old = getattr(self, "_cur_conn", None)
            if old is not None:
                self._hard_close(old)
            conn.request("GET", self.path, headers={
                "User-Agent": UA, "Accept": "*/*",
                "Range": "bytes=%d-" % start, "Icy-MetaData": "1"})
            r = conn.getresponse()
            if r.status not in (200, 206):
                raise UpstreamError("status %s" % r.status)
            self._cur_conn = conn
            self.strikes = 0
            cr = r.getheader("Content-Range") or ""
            m = re.match(r"bytes (\d+)-", cr)
            pos = int(m.group(1)) if m else start
            m2 = re.search(r"/(\d+)\s*$", cr)
            self.ctype = r.getheader("Content-Type") or self.ctype
            with self.lock:
                if m2 and not self.total:
                    self.total = int(m2.group(1))
                if self.gen != gen:
                    # 建流期间被重定：这条流作废，别拿它的原点去对齐环
                    stale_open = True
                else:
                    stale_open = False
                    if pos != self.read_pos \
                            or self.read_pos - self.base > len(self.buf):
                        # 流起点与环尾不一致：以流为准，重建环尾对齐
                        keep = max(0, pos - LOOKBACK - self.base)
                        del self.buf[:keep]
                        self.base = pos - len(self.buf)
                        self.read_pos = pos
                    self.log("ring: 泵流 bytes=%d-" % pos)
            if stale_open:
                self._hard_close(conn)
                time.sleep(0.1)
                self._pump_loop()
                return
            fed = 0
            while True:
                if self.disposed:
                    self._hard_close(conn)
                    return
                if self.gen != gen:
                    # 被重定：弃掉这条流，用新的 read_pos 重建
                    self.log("ring: 泵被重定，弃掉这条流 (t=%dMB)" % (pos >> 20))
                    self._hard_close(conn)
                    time.sleep(0.1)
                    self._pump_loop()
                    return
                try:
                    more = r.read(READ_BLOCK)
                except Exception:
                    more = None   # 静默 → 轮换
                    self.log("ring: 泵流静默(t=%dMB) → 轮换" % (pos >> 20))
                if more is None or more == b"":
                    if more == b"":
                        with self.lock:
                            if not self.total or pos >= self.total:
                                self.eof = True
                            else:
                                self.log("ring: 泵流提前EOF(t=%dMB) → 轮换"
                                         % (pos >> 20))
                    with self.lock:
                        if self.gen == gen:
                            self.read_pos = pos
                        self.lock.notify_all()
                    self._hard_close(conn)
                    if self.eof or self.disposed:
                        return
                    time.sleep(0.2)
                    self._pump_loop()
                    return
                with self.lock:
                    if self.gen != gen:
                        stale = True
                    else:
                        stale = False
                        self.buf += more
                        pos += len(more)
                        fed += len(more)
                        self.read_pos = pos
                        # 修剪：不早于 served-LOOKBACK，总量受 served+RING_MAX 约束
                        min_keep = self.served - LOOKBACK
                        if self.base < min_keep:
                            drop = min_keep - self.base
                            del self.buf[:drop]
                            self.base = min_keep
                        if self.read_pos - self.served > RING_MAX:
                            # 等 mpv 消费（环满；泵暂歇，代理静默由轮换兜底）
                            self.lock.wait(0.2)
                        self.lock.notify_all()
                if stale:
                    self.log("ring: 泵被重定，弃掉这条流 (t=%dMB)" % (pos >> 20))
                    self._hard_close(conn)
                    time.sleep(0.1)
                    self._pump_loop()
                    return
                if fed >= ROTATE:
                    self.log("ring: 泵流满 %dMB → 主动轮换" % (pos >> 20))
                    with self.lock:
                        if self.gen == gen:
                            self.read_pos = pos
                    self._hard_close(conn)
                    if self.disposed:
                        return
                    time.sleep(0.15)
                    self._pump_loop()
                    return
        except Exception as e:
            self.strikes += 1
            if self.strikes >= MAX_STRIKES:
                self.log("ring: 泵流连续 %d 次建流失败(%r)，泵停止" % (
                    self.strikes, e))
                with self.lock:
                    self.dead = True
                    self.lock.notify_all()
                return
            wait = min(2 ** self.strikes, 8)
            self.log("ring: 泵流建流失败 %r，%ds后重试(%d/%d)" % (
                e, wait, self.strikes, MAX_STRIKES))
            time.sleep(wait)
            try:
                self._pump_loop()
            except Exception as e2:
                self.log("ring: 泵流彻底失败 %r" % e2)
                with self.lock:
                    self.dead = True
                    self.lock.notify_all()

    # ---------- 旁路（跳读/回看，不动主泵） ----------
    def _bypass(self, start, length):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=45)
        try:
            conn.request("GET", self.path, headers={
                "User-Agent": UA, "Accept": "*/*",
                "Range": "bytes=%d-" % start, "Icy-MetaData": "1"})
            r = conn.getresponse()
            if r.status not in (200, 206):
                raise UpstreamError("bypass status %s" % r.status)
            self.ctype = r.getheader("Content-Type") or self.ctype
            data = b""
            while len(data) < length:
                more = r.read(min(READ_BLOCK * 4, length - len(data)))
                if not more:
                    break
                data += more
            return data
        finally:
            self._hard_close(conn)

    # ---------- 对 mpv ----------
    def wait_for(self, upto):
        """等环覆盖 upto；返回可覆盖到的上限。"""
        with self.lock:
            while self.read_pos < upto and not self.eof and not self.dead:
                self.lock.wait(0.25)
            return self.read_pos

    def get(self, start, end):
        """[start, end] 含端点。"""
        # 大跨度前跳：泵追不上，直接旁路（否则等 20s 才反应过来）
        with self.lock:
            if start > self.read_pos + 4 * CHUNK or start < self.base:
                why = ("前跳" if start > self.read_pos + 4 * CHUNK
                       else "回看已修剪")
                self.log("ring: get(%d) %s → 旁路 (read_pos=%d base=%d)" % (
                    start, why, self.read_pos, self.base))
                return self._bypass(start, min(end - start + 1, BYPASS_CHUNK))
        got = None
        for _ in range(80):          # 最多等 20s
            with self.lock:
                if self.base <= start < self.read_pos:
                    got = bytes(self.buf[start - self.base:
                                         min(end + 1, self.read_pos) - self.base])
                    break
                if start < self.base:
                    break            # 被修剪 → 旁路
            self.wait_for(start + 1)
        if got is None:
            self.log("ring: get(%d) 等 20s 仍无数据 → 旁路 (read_pos=%d base=%d"
                     " served=%d eof=%s dead=%s)" % (
                         start, self.read_pos, self.base, self.served,
                         self.eof, self.dead))
            return self._bypass(start, min(end - start + 1, BYPASS_CHUNK))
        # 环里有多少先给多少：缺口由泵继续追，调用方会续请求。
        # _bypass 只留给跳读/回看落空——它会并发开第二条上游流，代理扛不住。
        return got


def set_target(target_url, log=lambda m: None):
    global _current
    with _current_lock:
        old = _current
        if old is not None:
            old.disposed = True     # 让旧泵退出并 RST 掐线，释放代理会话
            _current = None
        try:
            _current = Pump(target_url, log)
        except Exception as e:
            log("ring: 初始化失败 %r" % e)
            _current = None
    return _current


_started = False


def ensure_started(port=RELAY_PORT, log=lambda m: None):
    global _started
    if _started:
        return
    _started = True
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _stream_from(self, pump, start, code=206):
            """把 [start, 文件尾) 作为一条长响应一路写下去。

            不能像短响应那样在 32MB 处收尾：mpv/ffmpeg 会把"响应正常结束"
            当成文件结束（实测只播十几秒就 EOF），所以开放式请求必须流到
            total；播放器跳转时会自己掐掉旧连接，这里按断开收尾即可。
            """
            if not pump.total:
                pump.wait_for(start + 1)      # 等首包，拿 ctype/total
            total = pump.total
            self.send_response(code)
            self.send_header("Content-Type", pump.ctype)
            self.send_header("Accept-Ranges", "bytes")
            if total:
                self.send_header("Content-Range",
                                 "bytes %d-%d/%d" % (start, total - 1, total))
                self.send_header("Content-Length", str(total - start))
            else:
                self.close_connection = True
                self.send_header("Connection", "close")
            self.end_headers()
            pos = start
            while True:
                data = pump.get(pos, pos + (8 << 20) - 1)
                if not data:
                    break
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    return          # 播放器跳走了，正常收场
                pos += len(data)
                if pos <= pump.served + RING_MAX:
                    pump.served = max(pump.served, pos)

        def do_GET(self):
            with _current_lock:
                pump = _current
            if pump is None:
                self.send_error(503, "no target")
                return
            pump.start()
            m = re.match(r"bytes=(\d+)-(\d*)",
                         self.headers.get("Range") or "")
            try:
                if m and m.group(2):
                    # bytes=X-Y（显式终点）：一次性短响应，够 ExoPlayer 式拉取
                    start = int(m.group(1))
                    end = min(int(m.group(2)), start + BYPASS_CHUNK - 1)
                    if pump.total:
                        end = min(end, pump.total - 1)
                    data = pump.get(start, end)
                    if not data:
                        log("ring: Range %d-%d 拿到空数据 → 只能回空响应 "
                            "(total=%s)" % (start, end, pump.total))
                    self.send_response(206)
                    self.send_header("Content-Type", pump.ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", "bytes %d-%d/%s" % (
                        start, start + len(data) - 1, pump.total or "*"))
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    # served 只跟顺序读走。mpv 为拿 mkv 索引会顺手读一次文件
                    # 尾部，若把这种远距离随机读记进 served，环的修剪基线会
                    # 瞬间跳到文件尾，之后所有正常读都被判成"回看已修剪"而
                    # 走旁路（曾导致播十几秒就断流）。
                    if start <= pump.served + RING_MAX:
                        pump.served = max(pump.served, start + len(data))
                    return
                # bytes=X- 或无 Range：开放式，一条长响应流到文件尾
                start = int(m.group(1)) if m else 0
                if pump.total:
                    if start > pump.read_pos + 4 * CHUNK \
                            and start < pump.total - (1 << 20):
                        # 真跳转（排除掉读文件尾索引那种随机读）：让泵跟到
                        # 新位置，否则整段播放都得靠旁路并发拉上游。
                        pump.retarget(start)
                    elif start + CHUNK < pump.base:
                        pump.retarget(start)      # 回看超出环
                self._stream_from(pump, start, 206 if m else 200)
            except Exception as e:
                log("ring: 请求失败 %r" % e)
                try:
                    self.send_error(502, str(e))
                except Exception:
                    pass

        def log_message(self, fmt, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("ring: 中继已监听 :%d (v5.1 轮换泵)" % port)
