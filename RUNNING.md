# PlayBridge 运行手册

本文只讲**怎么把它跑起来**。架构、设计取舍与实测结论见 [README.md](README.md)。

## 0. 组件与端口一览

| 组件 | 位置 | 端口 | 作用 |
|------|------|------|------|
| Android 端 | `FakePlayerProbe/` | — | 注册为视频外部播放器，收到 Intent 后 POST 到 Host |
| Host 控制面 | `PlayBridgeHost/playbridge_host.py` | `16888` | 收小 JSON，拉起 mpv |
| 数据面中继 | `PlayBridgeHost/ring_relay.py` | `18095` | 对 mpv 伪装成可 seek 的 206 源，维持到上游的长流 |
| adb 隧道 | 自动建立 | `18096 → 8096` | 把模拟器内影视仓代理映射到本机 |

数据流：`影视仓 → PlayBridge(Android) → POST :16888 → Host → ring_relay :18095 → adb 隧道 :18096 → 模拟器代理 :8096 → mpv`

## 1. 前置条件

- Windows 10/11
- Python 3（Host 只用标准库，无需 `pip install`）
- mpv（默认路径见下方常量表）
- MuMu 模拟器，且里面装有影视仓（TVBox 系）
- Android SDK platform-tools（提供 `adb`）
- 仅构建 Android 端时需要：JDK 17 + Gradle 7.6.4（AGP 7.4.2）

## 2. 启动 Windows Host

在 `PlayBridgeHost/` 下任选一种：

```powershell
python playbridge_host.py
```

或双击 `start_host.bat`。

启动后：

- 监听 `0.0.0.0:16888`，打印本机 IP。
- 每次收到播放请求时，若源是影视仓网盘代理，会自动执行
  `adb forward tcp:18096 tcp:8096`。
- 日志同时打印到控制台并写入 `PlayBridgeHost/playbridge.log`
  （控制台若重定向，通常会存成 `host_console.log`）。

连通性自测（在 Windows 上）：

```powershell
curl http://127.0.0.1:16888/ping
# {"ok": true, "service": "playbridge-host", "version": 1}
```

## 3. 安装 Android 端

**方式 A：Android Studio** —— 直接打开 `FakePlayerProbe/` 目录，运行到 MuMu 实例。

**方式 B：命令行**

```powershell
cd FakePlayerProbe
gradle :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

安装后打开 **PlayBridge**，把「服务器」填成 Windows Host 的地址：

- 模拟器能访问 `10.0.2.2` 时用 `10.0.2.2:16888`（App 默认值）。
- 否则填宿主机的局域网 IP，例如 `192.168.1.8:16888`（Host 启动日志里有）。

App 内点一下会自动保存到 SharedPreferences。

## 4. 播放

1. 确认 Host 已在运行。
2. 在影视仓里选片，点「外部播放」/「选择播放器」。
3. 选 **PlayBridge**。
4. Windows 上 mpv 自动弹出并开始缓冲播放；Host 控制台会打印
   `PLAY REQUEST`、`泵流 bytes=...` 等日志。

## 5. 按机器修改的常量

`PlayBridgeHost/playbridge_host.py` 顶部几个路径/端口是硬编码的，换机器要改：

| 常量 | 当前值 | 说明 |
|------|--------|------|
| `MPV` | `C:\Users\Administrator\AppData\Roaming\com.geon.quantumtv\mpv\mpv.exe` | mpv 可执行文件 |
| `ADB` | `C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe` | adb 路径 |
| `DEVICE` | `127.0.0.1:16416` | MuMu 实例的 adb 地址（用 `adb devices` 查） |
| `PROXY_PORT` | `8096` | 模拟器内影视仓代理端口 |
| `FORWARD_PORT` | `18096` | 本机隧道端口 |
| `PORT` | `16888` | 控制面端口 |

`FakePlayerProbe/local.properties` 里的 `sdk.dir` 也需指向本机 Android SDK。

## 6. 离线自测（不需要模拟器）

回归测试在 `PlayBridgeHost/tests/`，改完 `ring_relay.py` 建议先跑：

```powershell
cd PlayBridgeHost
python tests\test_relay.py            # 顺序流 / 回看命中 / 越界旁路
python tests\test_relay_recovery.py   # 上游短暂不可用后泵能自动恢复
python tests\test_relay_serial.py     # 对上游严格串行（同一时刻只有一条连接）
```

各自末尾打印 `RESULT: PASS` 即通过。

## 7. 观测与排障

### 观察一轮播放

```powershell
cd PlayBridgeHost
python watch_session.py 270 30   # 观察 270 秒，每 30 秒采样一次
```

输出：播放进度、前向缓存秒数、本段轮换次数、模拟器侧代理/影视仓 CPU、影视仓进程是否被杀。

### 日志

| 文件 | 内容 |
|------|------|
| `host_console.log` | Host 运行日志（播放请求、中继轮换、重连） |
| `playbridge.log` | 控制面 POST 记录 |
| `mpv.log` | mpv 详细日志（`--msg-level=curl=v,stream=v`） |
| `last_play.json` | 最近一次完整播放请求（含完整 URL），供离线调试（URL 会过期） |

### 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| mpv 报 `no target` / HTTP 503 | 还没收到播放请求就访问了中继；先在影视仓发起一次播放 |
| mpv 卡住或日志里 `TimeoutError`、`RemoteDisconnected` | 上游代理短暂不可用。中继会带退避持续重连，恢复后自动续传；若长时间不恢复，多半是代理被喂挂了，见下方「上游代理实测行为」 |
| 上游返回 `200 + 0 字节` | kaiser 代理按 User-Agent 白名单放行；确认走的是中继（会伪装 UA），不要用浏览器直接拉 |
| `adb devices` 为空 | 模拟器没起或端口变了；用 `adb connect 127.0.0.1:<port>`，并同步改 `DEVICE` |
| 换片/跳转后短暂停顿 | 正常：seek 后中继重定 + 预缓冲闸门在重新攒约 30s 缓存 |

### 上游代理实测行为（排障关键）

对上游直连压测得到：

- **一次只服务一条流**：并发请求会互相挤死（3 条并发只有 1 条拿到数据，另 2 条超时）。
- **每条流固定 ~60 秒寿命**，到点由代理主动 EOF，与字节数无关（全速约 1.6GB、6MB/s 约 370MB）。
- **顺序重连能把整部片子跑完**（全速 ×4 条，把 5.1GB 全下完，无挂死）。
- 被中途放弃的连接会在代理侧留下 `CLOSE_WAIT`、send-q 卡住若干字节；累积后代理不再应答任何请求。

因此中继对上游**严格串行化**：主泵与旁路（读 mkv 索引 / 跳转）不会并发，
需要旁路时先让泵断开，跑完再从原位置续传。每 ~60 秒代理 EOF 一次、中继顺序重连一次是正常节奏。

出现「播几分钟后所有重连都 `TimeoutError`」时，按顺序排查：

1. 确认代理是否已挂死（在 Windows 上）：
   ```powershell
   adb -s 127.0.0.1:16416 shell "netstat -tn | grep 8096"
   ```
   若出现多条 `CLOSE_WAIT` 且 send-q 卡着数字，代理多半已挂。
2. 重启影视仓清掉代理：
   ```powershell
   adb -s 127.0.0.1:16416 shell am force-stop com.huawei.himovceie
   adb -s 127.0.0.1:16416 shell am start -n com.huawei.himovceie/com.github.tvbox.osc.ui.activity.HomeActivity
   ```
3. 确认同一时刻只有一个客户端在连代理（Host 只开一个）。
4. 若怀疑中继又开了并发上游，跑 `python test_relay_serial.py`（应为 PASS）。

## 8. 停止

- 关掉 Host 窗口或按 `Ctrl+C`。
- 下一次播放请求会自动结束上一个 mpv 进程；也可直接关 mpv 窗口。
