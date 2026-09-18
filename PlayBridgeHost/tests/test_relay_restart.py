# -*- coding: utf-8 -*-
"""回归：上游持续不可用超过阈值时，必须调用"重启代理"钩子。

用一个必然拒绝连接的地址（127.0.0.1:1）让泵持续失败；把阈值调小，
断言 restart 钩子被调用。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ring_relay  # noqa: E402


def main():
    ring_relay.SILENT = 0.3
    ring_relay.MAX_STRIKES = 2
    ring_relay.RECONNECT_MAX = 0.5
    ring_relay.RECONNECT_HOOK_MIN = 999.0     # 不触发 adb 钩子
    ring_relay.PROXY_RESTART_AFTER = 1.0
    ring_relay.PROXY_RESTART_COOLDOWN = 0.0

    called = {"n": 0}
    ring_relay.set_proxy_restart_hook(
        lambda: called.__setitem__("n", called["n"] + 1))

    pump = ring_relay.set_target("http://127.0.0.1:1/kaiser", lambda m: None)
    pump.start()
    time.sleep(3.0)

    print("restart hook called = %d" % called["n"])
    ok = called["n"] >= 1
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
