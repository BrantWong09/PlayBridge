# PlayBridge

把 Android 端 TVBox/影视仓 的"外部播放器"意图桥到 Windows 本地播放器（mpv）。

> 只想把它跑起来？见 [RUNNING.md](RUNNING.md)（前置条件、启动、安装、排障）。

```
TVBox/影视仓 ──ACTION_VIEW──▶ PlayBridge (Android) ──POST /play──▶ PlayBridgeHost (Windows) ──▶ mpv
                                  (FakePlayerProbe)      控制面(小JSON)          数据面(adb forward + 本地环形中继)
```

## 组件

### FakePlayerProbe/ — Android 端（Java, Gradle）
注册为视频外部播放器（intent-filter: `VIEW` + 全 scheme + `video/*` MIME）。
收到 Intent 后提取 URI，向 Host POST 播放协议 v1：

```json
{ "version": 1, "action": "play", "url": "...", "title": "...", "position": 0, "extras": {} }
```

URL **原样搬运**，不解析、不改写（控制面/数据面分离）。默认服务器
`10.0.2.2:16888`（模拟器内访问宿主机），App 内可改并存 SharedPreferences。

### PlayBridgeHost/ — Windows 端（Python 标准库）
- `playbridge_host.py` — HTTP 服务 `:16888`（`GET /ping`、`POST /play`），
  拉起 mpv 播放。代理型源（`127.0.0.1:8096/kaiser?...`，影视仓网盘代理）
  先 `adb forward tcp:18096 tcp:8096`，再改写到 `18096` 交给中继。
- `ring_relay.py` — 本地数据面中继 `:18095`（v5.1 轮换泵）：
  对隧道只保持一条长流，静默/满 64MB 自动轮换重连续传；绝对偏移字节环
  （超前 200MB / 回看 24MB）；mpv 跳读超出环 → 旁路单发 Range 拉到即断。
  对 mpv 伪装成可 seek 的 206 源。
- `test_relay.py` — 中继离线回归测试（假代理：每条流只喂 3MB 后静默）。

## 关键机制（实测得出）

1. 影视仓对普通线路直接给可播 URL；对百度网盘则包一层本机 HTTP 代理
   `http://127.0.0.1:8096/kaiser?url=<真实直链>&thread=16&chunk=1024&key=baidupan`，
   header/UA/Cookie 全在代理内部注入，外部播放器拿不到也不需要。
2. kaiser 代理按 **User-Agent 白名单**区别响应：不认识的回 `200 + 0字节`，
   伪装 `com.android.chrome/... AndroidXMedia3/1.5.1` 才给 `206` 真数据。
3. 代理**只认持续消费**：背压暂停读会触发上游静默；每条流喂一段就静默，
   需按节奏轮换重开（v5.1 的泵）。
4. 真实百度直链绑定会话签名，Windows 端直接重放必 `sign error`——数据面
   必须经代理，这也是环形中继存在的意义。

## 构建与运行

Android：Android Studio 打开 `FakePlayerProbe/`，或
`gradle :app:assembleDebug`（AGP 7.4.2 / Gradle 7.6.4 / JDK 17，minSdk 21）。

Windows：`python playbridge_host.py`（无第三方依赖），或双击 `start_host.bat`。
模拟器需 `adb forward tcp:18096 tcp:8096`（Host 会自动执行）。
