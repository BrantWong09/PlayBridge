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
- 自动探测影视仓所在的模拟器 adb 地址：读 `adb devices`，按 `android_id` 去重
  同一实例的多个 transport，优先选装了影视仓的设备，日志形如
  `设备: 127.0.0.1:16384 (装了 com.huawei.himovceie)`。要强制指定就用环境变量
  `PLAYBRIDGE_DEVICE=127.0.0.1:16416`。
- 每次收到播放请求时，先把影视仓代理 URL 里裹着的**百度直链**解出来直连
  （日志 `直连上游（绕开模拟器代理与 adb 隧道）`）：这样数据面完全不经过模拟器。
  直连探测失败才回落老路，那时才 `adb forward tcp:18096 tcp:8096` 走隧道 + kaiser 代理。
  整体开关是 `playbridge_host.py` 的 `USE_DIRECT`。
- 数据面无论走哪条路都经过本地中继（`:18095`）：它的环形缓冲把 mpv 的
  「两个相距几百 MB 的位置交替读」变成内存里的范围读，这是流畅播放的关键。
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
| ~~`DEVICE`~~ | 启动时自动探测 | 不再写死。MuMu 重启后 adb 端口会变（实测同一实例在 16384/7555/5555 三个 transport 上，旧的 16416 直接拒连），写死会让 `adb forward` 静默失败、中继拿不到上游。要指定时用环境变量 `PLAYBRIDGE_DEVICE` |
| `USE_DIRECT` | `True` | 直连上游（绕开模拟器代理 + 隧道）；`False` 则所有播放都走 `/kaiser` + 隧道老路 |
| `MPV_VERBOSE` | 由 `PLAYBRIDGE_MPV_VERBOSE` 决定 | 置 `1` 才让 mpv 写 `mpv.log` |
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
| `playbridge.log` | 控制面 POST 记录 + 中继日志。重复消息按「数字归一化后的文本」折叠：同一个 key 5 秒内只输出一条，被压掉的次数附在下一条后面（`[同类消息又出现 N 次]`）。超过 2MB 时启动会归档成 `playbridge.log.1` |
| `mpv.log` | **默认不产生**。mpv 的 `--log-file` 不受 `--msg-level` 约束（实测 `--no-config --msg-level=all=error` 也照样写满 `[v]`/`[d]`，这个 mpv 是 debug 构建），一集 4K 片能刷几十 MB 的 `stream level seek`。要排查数据面时 `set PLAYBRIDGE_MPV_VERBOSE=1` 再启动 Host |
| `last_play.json` | 最近一次完整播放请求（含完整 URL），供离线调试（URL 会过期） |

### 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| mpv 报 `no target` / HTTP 503 | 还没收到播放请求就访问了中继；先在影视仓发起一次播放 |
| mpv 卡住或日志里 `TimeoutError`、`RemoteDisconnected` | 上游代理短暂不可用。中继会带退避持续重连，恢复后自动续传；若长时间不恢复，多半是代理被喂挂了，见下方「上游代理实测行为」 |
| 上游返回 `200 + 0 字节` | kaiser 代理按 User-Agent 白名单放行；确认走的是中继（会伪装 UA），不要用浏览器直接拉 |
| `adb devices` 为空 | 模拟器没起或 adb 端口变了；Host 会自动 `adb connect` 常见 MuMu 端口（16384/7555/5555/62001/62025）重试，且地址一旦失效下次播放请求会重新探测。要指定用 `PLAYBRIDGE_DEVICE` |
| 换片/跳转后短暂停顿 | 正常：seek 后中继重定 + 预缓冲闸门在重新攒约 30s 缓存 |

### 上游代理实测行为（排障关键）

对上游直连压测得到：

- **一次只服务一条流**：并发请求会互相挤死（3 条并发只有 1 条拿到数据，另 2 条超时）。
- **每条流固定 ~60 秒寿命**，到点由代理主动 EOF，与字节数无关（全速约 1.6GB、6MB/s 约 370MB）。
- **顺序重连能把整部片子跑完**（全速 ×4 条，把 5.1GB 全下完，无挂死）。
- 被中途放弃的连接会在代理侧留下 `CLOSE_WAIT`、send-q 卡住若干字节；累积后代理不再应答任何请求。

因此中继对上游**严格串行化**：主泵与旁路（读 mkv 索引 / 跳转）不会并发，
需要旁路时先让泵断开，跑完再从原位置续传。每 ~60 秒代理 EOF 一次、中继顺序重连一次是正常节奏。

### 代理看门狗（自动恢复）

代理一旦被喂挂就不会再应答，只能重启进程。Host 内置看门狗：上游**持续不可用
超过 `PROXY_RESTART_AFTER`（25s）** 时自动重启影视仓（`am force-stop` + 重新拉起），
重建 `new_go_proxy_wex`；重启后同一条直链继续播，播放器不用换源。
两次重启至少间隔 `PROXY_RESTART_COOLDOWN`（120s）。

- 日志标志：`ring: 上游持续不可用 → 重启上游代理` / `ring: 已重启影视仓以恢复上游代理`。
  若出现 `ring: am start 未拉起影视仓…改用 monkey` 属正常兜底：外部播放会把
  PlayBridge 的 Activity 压在影视仓 task 的栈顶，此时 `am start`（加不加 `-S`、
  `NEW_TASK` 都一样）会把 intent 投递给栈顶那个 Activity 而静默不启动影视仓，
  所以重启后以 `pidof` 为准、起不来就换 monkey 走 LAUNCHER intent。
- 相关常量：`ring_relay.py` 的 `PROXY_RESTART_AFTER` / `PROXY_RESTART_COOLDOWN`；
  影视仓包名/Activity 在 `playbridge_host.py` 的 `WUKONG_PKG` / `WUKONG_ACT`。
- 注意：看门狗会**强制重启影视仓**（它的界面会重置），但 mpv 播放会自动续上。

出现「播几分钟后所有重连都 `TimeoutError`」时，按顺序排查：

1. 先看日志有没有 `重启上游代理`——正常情况下看门狗已经处理，播放会自己恢复。
2. 若没有恢复，确认代理是否已挂死（在 Windows 上）：
   ```powershell
   adb -s 127.0.0.1:16416 shell "netstat -tn | grep 8096"
   ```
   若出现多条 `CLOSE_WAIT` 且 send-q 卡着数字，代理多半已挂。
3. 手动重启影视仓清掉代理：
   ```powershell
   adb -s 127.0.0.1:16416 shell am force-stop com.huawei.himovceie
   adb -s 127.0.0.1:16416 shell am start -n com.huawei.himovceie/com.github.tvbox.osc.ui.activity.HomeActivity
   ```
4. 确认同一时刻只有一个客户端在连代理（Host 只开一个）。
5. 若怀疑中继又开了并发上游，跑 `python tests\test_relay_serial.py`（应为 PASS）。

## 8. 停止

- 关掉 Host 窗口或按 `Ctrl+C`。
- 下一次播放请求会自动结束上一个 mpv 进程；也可直接关 mpv 窗口。
