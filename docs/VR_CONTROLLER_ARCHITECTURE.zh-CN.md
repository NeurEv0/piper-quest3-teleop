# VR 双臂遥操作系统 — 手柄连接架构详解

> 本文档详细说明 piper-quest3-teleop 项目中，Quest3 VR 头显的左右手柄如何与系统建立连接、数据如何流转、以及如何映射到 Piper 机械臂的运动控制。

---

## 目录

1. [整体架构概览](#1-整体架构概览)
2. [Quest3 VR 设备连接与配置教程](#2-quest3-vr-设备连接与配置教程)
	   - [2.5 ngrok 隧道模式（可选）](#25-ngrok-隧道模式可选)
	   - [2.6 USB 网络共享模式（无 WiFi 环境）](#26-usb-网络共享模式无-wifi-环境)
3. [手柄连接入口：OpenTeleVision](#3-手柄连接入口opentelevision)
4. [手柄事件处理：on_controller_move](#4-手柄事件处理on_controller_move)
5. [坐标预处理：Y-up → Z-up](#5-坐标预处理y-up--z-up)
6. [封装层：VuerTeleop](#6-封装层vuerteleop)
7. [VR→机器人位姿映射](#7-vr机器人位姿映射)
8. [左右手柄控制器逻辑](#8-左右手柄控制器逻辑)
9. [状态机与 IK 求解](#9-状态机与-ik-求解)
10. [双臂模式配置](#10-双臂模式配置)
11. [手柄按键功能汇总](#11-手柄按键功能汇总)
12. [关键文件清单](#12-关键文件清单)
13. [完整数据流管线图](#13-完整数据流管线图)

---

## 1. 整体架构概览

该系统通过 **Quest3 VR 头显** 的两个手柄（左手+右手）实现对一台或两台 **Piper 机械臂** 的遥操作控制。系统有两套并行的入口路径：

| 路径 | 入口文件 | 用途 |
|------|----------|------|
| **独立遥操作** | `teleop/teleop_real_arm.py` | 直接使用 Vuer + MuJoCo/MINK 的实时遥操作（右手柄控制单臂） |
| **LeRobot 集成** | `lerobot-record` 命令 | 标准化数据采集，支持单臂 (`quest3_vr`) 和双臂 (`bi_quest3_vr`) |

---

## 2. Quest3 VR 设备连接与配置教程

本节详细介绍如何将 Meta Quest3 头显连接到此遥操作系统。**仅支持 Quest3 设备**，因为系统依赖 Quest3/WebXR 标准的 MotionControllers API 和 Y-up 坐标系。

### 2.1 前置条件

**硬件要求：**

- Meta Quest3 头显 ×1（含左右手柄）
- 运行遥操作服务的主机（Linux，需与 Quest3 处于同一局域网）
- 建议使用 5GHz WiFi 以获得更低的延迟

**软件要求：**

- 主机已安装 conda 环境并配置好本项目依赖（`conda activate lerobot`）
- Quest3 上已安装支持 WebXR 的浏览器（Quest3 系统 Meta Quest Browser 默认支持）
- OpenTeleVision VR 应用（WebXR 网页应用，通过浏览器访问，无需安装 APK）

### 2.2 网络配置 —— 设置服务器 IP 地址

Quest3 通过局域网 HTTPS/WebSocket 连接到服务器，需要将服务器的 LAN IP 配置到代码中。

**步骤：**

1. 在 Linux 主机上查看局域网 IP 地址：
   ```bash
   ip addr show | grep "inet " | grep -v 127.0.0.1
   ```
   典型输出如 `192.168.1.100`。

2. 编辑 [teleop/TeleVision.py](teleop/TeleVision.py)，将第 32 行和第 34 行的 `[IP]` 占位符替换为实际的服务器 IP：
   ```python
   # 第 32 行（ngrok 模式）
   self.app = Vuer(host='192.168.1.100', queries=dict(grid=False), queue_len=3)

   # 第 34 行（直连 HTTPS 模式）
   self.app = Vuer(host='192.168.1.100', cert=cert_file, key=key_file,
                   queries=dict(grid=False), queue_len=3)
   ```

3. 确保主机防火墙允许 Vuer 使用的端口（Vuer 库内部决定端口号，通常为 8012）：
   ```bash
   sudo ufw allow 8012/tcp
   ```

> **注意**：Quest3 和服务器主机**必须在同一局域网**内。不支持跨公网直连（除非使用 ngrok 隧道模式，见 2.5 节）。

### 2.3 HTTPS 证书生成

Quest3 的 WebXR API 要求 HTTPS 连接。系统使用自签名证书即可——Quest3 浏览器允许手动信任自签名证书。

**步骤：**

1. 使用 OpenSSL 生成自签名证书（有效期 365 天）：
   ```bash
   cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop/teleop
   openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
     -days 365 -nodes \
     -subj "/CN=192.168.1.100"
   ```
   将 `CN=` 后面的 IP 地址替换为服务器的实际 LAN IP。

2. 验证证书文件已生成：
   ```bash
   ls -la teleop/cert.pem teleop/key.pem
   ```

3. 证书文件已在 `.gitignore` 中排除（`*.pem`），不会被提交到版本控制。

**证书文件位置：**

| 文件 | 默认路径 | 说明 |
|------|----------|------|
| `cert.pem` | `teleop/cert.pem` | 自签名 X.509 证书 |
| `key.pem` | `teleop/key.pem` | RSA 私钥（2048 位） |

[teleop/TeleVision.py](teleop/TeleVision.py) 第 18–24 行的证书路径解析逻辑：

```python
def __init__(self, ..., cert_file="./cert.pem", key_file="./key.pem", ...):
    base_dir = Path(__file__).resolve().parent
    cert_path = (base_dir / cert_file).resolve()  # 相对于 teleop/ 目录解析
    key_path  = (base_dir / key_file).resolve()
```

> **安全提示**：自签名证书仅适用于局域网内的开发和测试环境，请勿在公网环境下使用。

### 2.4 Quest3 头显端配置

OpenTeleVision 是一个 WebXR 网页应用，无需安装 APK，直接通过 Quest3 系统浏览器访问。

**步骤：**

1. 戴上 Quest3 头显，确保已连接到与服务器**相同的 WiFi 网络**。

2. 打开 Quest3 系统浏览器（Meta Quest Browser）。

3. 在地址栏输入 Vuer 服务器的 HTTPS URL：
   ```
   https://10.200.5.229:8012
   ```
   将 IP 和端口替换为实际值。

4. **首次访问时的证书警告**：由于使用自签名证书，浏览器会显示安全警告。点击"高级"（Advanced），然后选择"继续前往"（Proceed to ...）以信任证书。此操作仅需执行一次。

5. 进入页面后，Vuer 会自动建立 WebSocket 连接（URL 中包含 `?ws=wss://...` 参数），并启动 WebXR 沉浸式会话。浏览器可能弹出"进入 VR 模式"的提示，点击"允许"（Allow）。

6. 确认连接成功：
   - 头显中应看到 VR 场景（默认显示空场景，`grid=False` 关闭了网格线）
   - 如果启用了相机画面流（`stream_camera_to_headset=true`），应看到双目相机画面
   - 左右手柄的 6-DoF 运动和按键操作应被实时捕获

**连接 URL 格式说明（参考 [VuerTeleop.py:51](teleop/VuerTeleop.py#L51)）：**

```
https://<服务器IP>:<端口>?ws=wss://<服务器IP>:<端口>
```

例如：`https://192.168.1.100:8012?ws=wss://192.168.1.100:8012`

Vuer 使用同一端口同时处理 HTTPS 页面请求和 WebSocket (WSS) 实时数据流。`queue_len=3` 限制事件队列长度以防止延迟累积。

### 2.5 ngrok 隧道模式（可选）

如果无法在局域网内直连（例如服务器和 Quest3 不在同一网段），可以使用 ngrok 隧道。

编辑 [teleop/TeleVision.py](teleop/TeleVision.py)，在初始化 `OpenTeleVision` 时设置 `ngrok=True`（第 31–32 行的逻辑），ngrok 会提供公网 HTTPS 地址，Quest3 通过该公网地址连接。此模式下不需要本地 `cert.pem`/`key.pem`。

> **注意**：使用 ngrok 会引入额外延迟，不建议用于精细遥操作任务。


### 2.6 USB 网络共享模式（无 WiFi 环境）

如果服务器和 Quest3 所在环境没有可用的 WiFi 网络，可以通过 **USB 数据线 + 反向网络共享** 的方式建立连接：

- **gnirehtet**（反向网络共享工具）通过 ADB/USB 将主机的互联网连接共享给 Quest3，使 Quest3 能够加载 Vuer 前端页面
- **adb reverse** 将 Quest3 的 `localhost:8012` 端口转发到主机的 `8012` 端口，使 WebSocket 数据流通过 USB 传输

> **适用场景**：无 WiFi 环境；主机有 VPN 且可开热点但网卡不支持 AP 模式；需要低延迟有线连接。

#### 2.6.1 前置条件

- Quest3 已开启**开发者模式**和 **USB 调试**
- 主机已安装 `adb`（Android Debug Bridge）
- 主机已安装 Java 运行时（JRE 21+）
- Quest3 通过 USB-C 数据线连接至主机

#### 2.6.2 安装 gnirehtet

gnirehtet 是 Genymobile 开发的 Android 反向网络共享工具，通过 ADB 将主机网络共享给 Android 设备。

1. 安装 Java 运行时（如未安装）：
   ```bash
   sudo apt update && sudo apt install -y default-jre
   ```

2. 下载 gnirehtet（最新版本 v2.5.1）：
   ```bash
   mkdir -p ~/.local/opt/gnirehtet ~/.local/bin
   curl -fL --retry 3 \
     -o /tmp/gnirehtet-rust-linux64-v2.5.1.zip \
     https://github.com/Genymobile/gnirehtet/releases/download/v2.5.1/gnirehtet-rust-linux64-v2.5.1.zip
   # 校验 SHA-256
   printf '%s  %s\n' \
     dee55499ca4fef00ce2559c767d2d8130163736d43fdbce753e923e75309c275 \
     /tmp/gnirehtet-rust-linux64-v2.5.1.zip \
     | sha256sum --check
   unzip -o /tmp/gnirehtet-rust-linux64-v2.5.1.zip \
     -d ~/.local/opt/gnirehtet
   ```

#### 2.6.3 生成 localhost 证书

由于 Quest3 浏览器通过 `localhost` 访问 Vuer 服务器（经 adb reverse 转发），证书必须包含 `localhost`：

```bash
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop/teleop

# 备份旧证书
cp cert.pem cert.pem.pre-usb
cp key.pem key.pem.pre-usb

# 生成包含 localhost SAN 的证书
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 365 \
  -keyout key.pem -out cert.pem \
  -subj '/CN=localhost' \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
chmod 600 key.pem
```

#### 2.6.4 配置 Vuer 监听地址

编辑 [teleop/TeleVision.py](teleop/TeleVision.py)，将 Vuer 的 `host` 参数改为 `0.0.0.0`（监听所有网络接口）：

```python
# 第 32 行和第 34 行
self.app = Vuer(host='0.0.0.0', ...)
```

#### 2.6.5 启动连接

**每次连接需按以下顺序操作：**

```bash
# 1. 确认 Quest3 USB 连接正常
adb devices -l
# 应显示: 2G97C5ZHCS04C7  device  ...  model:Quest_3

# 2. 启动 gnirehtet 反向网络共享（新终端，保持运行）
~/.local/opt/gnirehtet/gnirehtet-rust-linux64/gnirehtet run 2G97C5ZHCS04C7
# Quest3 端会弹出 VPN 连接请求，点击"允许"

# 3. 配置端口转发
adb -s 2G97C5ZHCS04C7 reverse tcp:8012 tcp:8012

# 4. 启动 Vuer 服务
conda activate lerobot
cd ~/ZHITAI_1t/piper-quest3-teleop
python -m teleop.teleop_real_arm --dry-run

# 5. Quest3 浏览器先访问 https://localhost:8012 信任证书，
#    再打开 https://vuer.ai?ws=wss://localhost:8012
```

> **注意**：URL 中必须使用 `localhost` 而非主机 IP，因为 `adb reverse` 将 Quest3 端的 `localhost:8012` 转发到主机的 `8012` 端口。

#### 2.6.6 故障排查

| 问题 | 可能原因 | 解决方法 |
|------|----------|----------|
| Quest3 无法加载 vuer.ai | gnirehtet 未运行或未授权 | 重新启动 gnirehtet，在 Quest3 弹出的 VPN 对话框中点"允许" |
| WebSocket 连接失败 | adb reverse 未配置 | 执行 `adb reverse tcp:8012 tcp:8012`，用 `adb reverse --list` 确认 |
| 证书警告无法跳过 | 证书不包含 localhost | 按 2.6.3 重新生成证书 |
| adb devices 显示 no permissions | udev 规则缺失 | 重新插拔 USB 线，或执行 `adb kill-server && adb start-server` |
| gnirehtet 启动报错 | Java 未安装 | `sudo apt install -y default-jre` |
### 2.7 验证连接

#### 2.7.1 测试 Vuer 进程启动

运行独立遥操作脚本，确认 Vuer 服务器正常启动：

```bash
conda activate lerobot
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop

# dry-run 模式（不连接真实机械臂），仅测试 VR 连接
python -m teleop.teleop_real_arm --dry-run
```

预期输出中应包含 Vuer 启动日志，无证书相关错误。

#### 2.7.2 确认手柄数据流通

在 Quest3 已连接的状态下，观察终端输出中的手柄位姿和按键状态数据（可通过 `--print-freq` 标志打印频率信息）：

```bash
python -m teleop.teleop_real_arm --dry-run --print-freq
```

用手柄做动作，终端应输出实时位姿数据，确认 `CONTROLLER_MOVE` 事件正常触发。

#### 2.7.3 Mock VR 模式（无 Quest3 时）

若暂时没有 Quest3 设备，可以使用 Mock VR 模式测试整个管线：

```bash
# LeRobot 单臂 mock 测试
lerobot-record \
  --robot.type=piper_quest3 \
  --teleop.type=quest3_vr \
  --teleop.mock_vr=true \
  --dataset.repo_id=local/test \
  --dataset.root=/tmp/test_vr \
  --dataset.push_to_hub=false \
  --dataset.num_episodes=1 \
  --dataset.episode_time_s=10

# LeRobot 双臂 mock 测试
lerobot-record \
  --robot.type=bi_piper_quest3 \
  --teleop.type=bi_quest3_vr \
  --teleop.mock_vr=true \
  --dataset.repo_id=local/test_bimanual \
  --dataset.root=/tmp/test_bimanual \
  --dataset.push_to_hub=false \
  --dataset.num_episodes=1 \
  --dataset.episode_time_s=10
```

### 2.8 启动遥操作

#### 2.8.1 单臂模式（右手柄 → 右臂）

```bash
conda activate lerobot
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop

# 使用便捷脚本（推荐）
bash scripts/record_single_arm_vr.sh

# 或手动指定参数
lerobot-record \
  --robot.type=piper_quest3 \
  --robot.can_name=can_right \
  --teleop.type=quest3_vr \
  --teleop.mock_vr=false \
  --teleop.stream_camera_to_headset=false \
  --display_data=true \
  --dataset.repo_id=local/piper_quest3_demo \
  --dataset.root=/home/ylhp-e-ai/ZHITAI_1t/piper_lerobot-data/quest3_demo \
  --dataset.push_to_hub=false \
  --dataset.num_episodes=20 \
  --dataset.episode_time_s=60 \
  --dataset.reset_time_s=30 \
  --dataset.single_task="Pick and place the cube"
```

#### 2.8.2 双臂模式（左右手柄 → 左右臂）

```bash
conda activate lerobot
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop

# 使用便捷脚本（推荐）
bash scripts/record_bimanual_vr.sh

# 或手动指定参数
lerobot-record \
  --robot.type=bi_piper_quest3 \
  --robot.left_can_name=can_left \
  --robot.right_can_name=can_right \
  --teleop.type=bi_quest3_vr \
  --teleop.mock_vr=false \
  --teleop.stream_camera_to_headset=false \
  --display_data=false \
  --dataset.repo_id=local/piper_bimanual_vr_demo \
  --dataset.root=/home/ylhp-e-ai/ZHITAI_1t/piper_lerobot-data/bimanual_vr_demo \
  --dataset.push_to_hub=false \
  --dataset.num_episodes=20 \
  --dataset.episode_time_s=60 \
  --dataset.reset_time_s=30 \
  --dataset.single_task="stack the cube"
```

> **重要提示**：数采时建议设置 `--teleop.stream_camera_to_headset=false`，避免 JPEG 编码推流与 Orbbec 相机争抢 CPU/内存带宽，从而保证录制帧率稳定。

### 2.9 VR 遥操作配置参数速查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--teleop.type` | 必填 | `quest3_vr`（单臂）或 `bi_quest3_vr`（双臂） |
| `--teleop.mock_vr` | `false` | 设为 `true` 可在无 Quest3 时测试管线 |
| `--teleop.gripper_alpha` | `0.35` | 夹爪 trigger 的 EMA 平滑系数（0=不过滤，1=完全平滑） |
| `--teleop.gripper_max_m` | `0.07` | 夹爪最大开度（米），默认 70mm |
| `--teleop.enable_skeleton` | `true`（单臂）/ `false`（双臂） | 在头显中渲染机械臂骨架叠加层 |
| `--teleop.stream_camera_to_headset` | `true` | 将相机画面推流到头显（数采时建议关闭） |

### 2.10 常见问题排查

| 问题 | 可能原因 | 解决方法 |
|------|----------|----------|
| Quest3 浏览器无法打开页面 | 证书未信任 / IP 不可达 | 检查 WiFi 是否同网段，确认防火墙放行端口，手动信任自签名证书 |
| 手柄数据不更新 | WebSocket 连接断开 | 刷新 Quest3 浏览器页面，检查 Vuer 进程是否存活 |
| `cert.pem not found` 错误 | 未生成证书 | 按照 2.3 节步骤生成并放置证书文件 |
| 手柄位姿抖动/延迟大 | WiFi 信号弱或 2.4GHz 干扰 | 切换到 5GHz WiFi，靠近路由器，减少中间障碍物 |
| Vuer 进程无法绑定端口 | 端口被占用 | 检查是否有残留 Vuer 进程（`ps aux \| grep vuer`），kill 后重试 |
| `CONTROLLER_MOVE` 无回调 | MotionControllers 组件未注册 | 确认 `MotionControllers(stream=True, left=True, right=True)` 已在 session 中注册 |
| 相机画面不显示 | `stream_images` 未启用 | 检查 `stream_camera_to_headset=true`，确认相机设备已连接 |
| 证书过期 | 自签名证书超过 365 天 | 重新生成证书（见 2.3 节） |

---

## 3. 手柄连接入口：OpenTeleVision

**文件**: [teleop/TeleVision.py](teleop/TeleVision.py) (第 18–105 行)

`OpenTeleVision` 类是**整个手柄连接的核心**。它在初始化时启动一个 **Vuer WebSocket 服务器**，Quest3 头显端的 VR 应用通过 HTTPS/WebSocket 连接到该服务器。

### 关键初始化代码

```python
# 第 34 行 — 创建 Vuer 服务器实例，使用 HTTPS 证书
self.app = Vuer(host='[IP]', cert=cert_file, key=key_file,
                queries=dict(grid=False), queue_len=3)

# 第 37 行 — 注册手柄事件处理器（数据流入系统的唯一入口）
self.app.add_handler("CONTROLLER_MOVE")(self.on_controller_move)

# 第 53 行 — 启动 Vuer session 协程
# 第 234 行 — 注册 MotionControllers 组件，启用左右手柄流式数据
session.upsert @ MotionControllers(stream=True, ..., left=True, right=True)
```

### 进程模型

Vuer 服务在**独立的后台守护进程**中运行（第 103–105 行）：

```python
self.process = Process(target=self.run)
self.process.daemon = True
self.process.start()
```

---

## 4. 手柄事件处理：on_controller_move

**文件**: [teleop/TeleVision.py](teleop/TeleVision.py) (第 112–178 行)

这是**手柄数据流入系统的唯一入口**。每当 Quest3 手柄移动或按键状态变化，Vuer 服务器触发 `CONTROLLER_MOVE` 事件，数据通过 WebSocket 传入。

### 右手柄处理（第 116–144 行）

```python
# 读取 4×4 位姿矩阵（16 个浮点数），存入共享内存
right_mat = np.array(event.value["right"]).reshape(4, 4).T
right_controller_shared[:] = right_mat.flatten()

# 读取按键/摇杆/扳机状态，解析为 14 维数组
right_state[0]  = 1.0 if event.value["rightState"]["trigger"] else 0.0     # trigger 按下
right_state[1]  = 1.0 if event.value["rightState"]["squeeze"] else 0.0     # squeeze 按下
right_state[2]  = 1.0 if event.value["rightState"]["touchpad"] else 0.0    # touchpad 按下
right_state[3]  = 1.0 if event.value["rightState"]["thumbstick"] else 0.0  # thumbstick 按下
right_state[4]  = 1.0 if event.value["rightState"]["a"] else 0.0           # A 按钮
right_state[5]  = 1.0 if event.value["rightState"]["b"] else 0.0           # B 按钮
right_state[6]  = float(event.value["rightState"]["triggerValue"])          # trigger 值 (0-1)
right_state[7]  = float(event.value["rightState"]["squeezeValue"])          # squeeze 值
right_state[8]  = float(event.value["rightState"]["touchpadX"])             # touchpad X
right_state[9]  = float(event.value["rightState"]["touchpadY"])             # touchpad Y
right_state[10] = float(event.value["rightState"]["thumbstickX"])           # thumbstick X
right_state[11] = float(event.value["rightState"]["thumbstickY"])           # thumbstick Y
right_state[12] = 1.0 if event.value["rightState"]["aValue"] else 0.0      # A 按钮值
right_state[13] = 1.0 if event.value["rightState"]["bValue"] else 0.0      # B 按钮值
```

### 右手柄状态数组布局

| 索引 | 含义 | 类型 | 用途 |
|------|------|------|------|
| 0 | trigger 按下 | bool (0/1) | — |
| **1** | **squeeze 按下** | bool (0/1) | **进入/退出遥操作** |
| 2 | touchpad 按下 | bool (0/1) | — |
| 3 | thumbstick 按下 | bool (0/1) | — |
| **4** | **A 按钮** | bool (0/1) | **返回零位** |
| 5 | B 按钮 | bool (0/1) | 预留 |
| **6** | **trigger 值** | float (0-1) | **夹爪控制** |
| 7 | squeeze 值 | float | — |
| 8-9 | touchpad x, y | float | — |
| 10-11 | thumbstick x, y | float | — |
| 12-13 | a/b 按钮值 | bool (0/1) | — |

### 左手柄处理（第 146–174 行）

左手柄与右手柄使用**完全相同的 14 维状态布局**，从 `event.value["left"]` 和 `event.value["leftState"]` 读取数据。

### 共享内存与属性访问（第 57–62 行、第 301–328 行）

手柄数据存储在 `multiprocessing.Array` 共享内存中，供跨进程读取：

| 共享内存变量 | 类型 | 内容 |
|-------------|------|------|
| `right_controller_shared` | `Array('d', 16)` | 右手 4×4 位姿矩阵 |
| `right_state_shared` | `Array('d', 14)` | 右手按键状态 |
| `left_controller_shared` | `Array('d', 16)` | 左手 4×4 位姿矩阵 |
| `left_state_shared` | `Array('d', 14)` | 左手按键状态 |

通过属性访问器（`self.right_controller`、`self.right_state` 等）将共享内存转为 NumPy 数组对外暴露。

---

## 5. 坐标预处理：Y-up → Z-up

**文件**: [teleop/Preprocessor.py](teleop/Preprocessor.py) (第 22–30 行)

VR 使用 **Y-up 坐标系**（Quest3/WebXR 标准），而机器人控制使用 **Z-up 坐标系**。`VuerPreprocessor` 完成此转换。

### 变换矩阵定义

**文件**: [teleop/constants_vuer.py](teleop/constants_vuer.py) (第 11–14 行)

```python
grd_yup2grd_zup = np.array([
    [0, 0, -1, 0],
    [-1, 0, 0, 0],
    [0, 1, 0, 0],
    [0, 0, 0, 1]
])
```

### 变换公式

```python
# 第 28 行
new_matrix = T @ old_matrix @ T_inv
```

### 双臂预处理

`process_both()` 方法（第 38–40 行）同时处理左右手柄的坐标变换。

---

## 6. 封装层：VuerTeleop

**文件**: [teleop/VuerTeleop.py](teleop/VuerTeleop.py)

`VuerTeleop` 是上层代码访问手柄数据的统一封装接口。

### 初始化（第 14–59 行）

```python
def __init__(self, img_shape=None, enable_obs_buffer=False, img_shm=None):
    # 创建共享内存（可选图像缓冲区）
    # 启动 OpenTeleVision 服务器
    # 启动 VuerPreprocessor
```

### 单臂接口（第 62–75 行）

```python
def step(self):
    """读取右手柄数据，坐标变换后返回 [x,y,z,qx,qy,qz,qw] 姿态"""
    mat = self.tv.right_controller  # 4x4 矩阵
    mat = self.preprocessor.process(mat)
    pose7 = matrix_to_pose7(mat)    # → [x,y,z,qx,qy,qz,qw]
    return pose7
```

### 双臂接口（第 84–91 行）

```python
def step_both(self):
    """同时获取左右手柄姿态，返回 (left_pose7, right_pose7)"""
    left_mat = self.preprocessor.process(self.tv.left_controller)
    right_mat = self.preprocessor.process(self.tv.right_controller)
    return matrix_to_pose7(left_mat), matrix_to_pose7(right_mat)
```

### 状态访问器（第 93–98 行）

```python
@property
def right_state(self):  # 返回右手柄 14 维状态数组
@property
def left_state(self):   # 返回左手柄 14 维状态数组
```

---

## 7. VR→机器人位姿映射

**文件**: [teleop/mapping/vr_mapper.py](teleop/mapping/vr_mapper.py)

`VRToRobotMapper` 类实现手柄相对运动到机器人末端目标位姿的映射。

### 位置映射（第 163–165 行）

```
mapped_pos = scale × (vr_pos − vr_neutral_pos) + base_pos
```

### 旋转映射（第 184–191 行）

1. 计算 VR 相对旋转：`dR_vr = R_vr0^T @ R_vr`
2. 通过轴映射矩阵 `P` 转换（第 188 行）：`dR_robot = P @ dR_vr @ P^T`
3. 最终目标旋转：`R_target = base_R @ dR_robot`

### 轴映射矩阵 P（第 27–31 行）

```
X_robot ← -Z_vr
Y_robot ←  Y_vr
Z_robot ←  X_vr
```

### 中性位姿校准（第 92–123 行）

按下 squeeze 时捕获当前手柄位姿作为新的**零参考点**，此机制支持从 HOLD 状态重新进入遥操作，保证相对运动的连续性。

---

## 8. 左右手柄控制器逻辑

### 右手柄：RightController

**文件**: [teleop/runtime/init_right_controller.py](teleop/runtime/init_right_controller.py) (第 62–239 行)

处理右手柄的遥操作核心交互：

| 操作 | 代码位置 | 行为 |
|------|----------|------|
| **Squeeze 按住** | 第 144–148 行 | 进入 TELEOP 模式 |
| **Squeeze 松开** | 第 144–148 行 | 进入 HOLD 模式（冻结当前位置） |
| **A 按钮按下** | 第 153–157 行 | 触发 RETURNING（返回零位） |
| **Trigger 模拟量** | 第 199–212 行 | 夹爪控制，支持 analog/toggle 模式 |
| **EMA 平滑** | 第 214–224 行 | `alpha=0.35` 对 trigger 值做指数滑动平均 |

### 左手柄：LeftController

**文件**: [teleop/runtime/init_left_controller.py](teleop/runtime/init_left_controller.py) (第 29–95 行)

当前用于 **Vision60 移动平台的 ROS2 遥控**：

| 操作 | 代码位置 | 行为 |
|------|----------|------|
| thumbstick x | 第 62–66 行 | 控制角速度 |
| trigger/squeeze | 第 62–66 行 | 控制线速度 |
| Y 按钮 | 第 69–78 行 | 切换 Vision60 动作模式 |

---

## 9. 状态机与 IK 求解

### 状态机流程

```
             ┌──────────────────────────────────┐
             │                                  │
             ▼                                  │
┌──────────────┐  关节接近零位   ┌──────────────┐ │
│  RETURNING   │ ────────────► │   AT_ZERO    │ │
│  (返回零位)   │               │  (在零位等待)  │ │
└──────────────┘               └──────┬───────┘ │
      ▲                               │ squeeze │
      │ A按钮                         ▼ 按下    │
      │                      ┌──────────────┐   │
      │                      │   TELEOP     │   │
      │                      │  (遥操作中)   │   │
      │                      └──────┬───────┘   │
      │                             │ squeeze   │
      │                      ┌──────▼───────┐   │
      └──────────────────────│    HOLD      │───┘
                             │  (保持位置)   │
                             └──────────────┘
```

### 独立遥操作状态机

**文件**: [teleop/app.py](teleop/app.py) (第 192–288 行)

### LeRobot 集成状态机

**文件**: [teleop/vr_arm_engine.py](teleop/vr_arm_engine.py) (第 225–274 行)

`ArmVREngine` 是可复用的单臂 VR 引擎，对每一帧执行：

1. 解析按键状态（squeeze/return/trigger） — 第 200–208 行
2. 更新夹爪 EMA — 第 211–213 行
3. 状态机决策 — 第 225–274 行
4. 映射 VR 姿态到 EE 目标 — 第 262–266 行
5. MINK IK 求解 — 第 277–295 行
6. 返回 7-DoF 动作字典 — 第 301–305 行

### MINK IK 求解

**文件**: [teleop/control/ik_stepper.py](teleop/control/ik_stepper.py) (第 1–70 行)

```python
def ik_step(model, data, configuration, tasks, limits, solver, dt,
            last_q, target_T_use, grip_hw, q_idx7, q_idx8, ...):
    # 1. 限幅 dt (1e-4 ~ 1/30)
    # 2. 同步 MuJoCo 状态 + 前向运动学
    # 3. 计算夹爪关节角 (joint7, joint8)
    # 4. 设置末端执行器目标任务
    # 5. mink.solve_ik() 求解速度
    # 6. configuration.integrate_inplace() 积分更新
    # 7. 返回新的 6 轴关节角
```

---

## 10. 双臂模式配置

**文件**: [lerobot_teleoperator_bi_quest3_vr/bi_quest3_vr.py](lerobot_teleoperator_bi_quest3_vr/bi_quest3_vr.py) (第 46–175 行)

### 核心映射（第 68–69 行）

```python
# 左手柄 → 左臂，右手柄 → 右臂
self._left_engine = ArmVREngine(controller_side="left", name="left", ...)
self._right_engine = ArmVREngine(controller_side="right", name="right", ...)
```

### get_action() 流程（第 136–164 行）

```python
def get_action(self):
    # 1. 同时获取左右手柄位姿
    left_pose, right_pose = self._vuer.step_both()

    # 2. 分别驱动左右引擎
    left_action = self._left_engine.step(left_pose, left_state)
    right_action = self._right_engine.step(right_pose, right_state)

    # 3. 合并为 14-DoF 动作（6+6 关节 + 2 夹爪）
    action = {
        "left_joint_1.pos":  ..., ..., "left_joint_6.pos":  ...,
        "right_joint_1.pos": ..., ..., "right_joint_6.pos": ...,
        "left_gripper.pos":  ..., "right_gripper.pos": ...,
    }
    return action
```

### 双臂配置文件

**文件**: [lerobot_teleoperator_bi_quest3_vr/config_bi_quest3_vr.py](lerobot_teleoperator_bi_quest3_vr/config_bi_quest3_vr.py)

```python
@dataclass
class BiQuest3VRConfig:
    can_left: str = "can_left"       # 左臂 CAN 接口
    can_right: str = "can_right"     # 右臂 CAN 接口
    enable_skeleton: bool = False    # 骨架叠加（双臂下禁用，因只跟踪单个锚点）
    # ... 其他配置
```

> **注意**：双臂模式下每个手柄各自运行一套完全独立的状态机。两条臂可以处于不同的状态（例如左臂 HOLD 而右臂 TELEOP），互不干扰。

---

## 11. 手柄按键功能汇总

### 右手柄（遥操作核心控制）

| 按键 | 功能 | 状态机影响 |
|------|------|-----------|
| **Squeeze（按住）** | 进入遥操作模式 | AT_ZERO / HOLD → TELEOP |
| **Squeeze（松开）** | 冻结当前位置 | TELEOP → HOLD |
| **A 按钮** | 返回机械零位 | HOLD → RETURNING |
| **Trigger（模拟量 0-1）** | 夹爪开合控制（EMA α=0.35） | — |
| **手柄 6-DoF 姿态** | 控制机械臂末端执行器位姿 | 仅在 TELEOP 模式有效 |
| B 按钮 | 预留 | — |

### 左手柄（Vision60 移动平台遥控）

| 按键 | 功能 |
|------|------|
| Thumbstick X | 角速度控制 |
| Trigger | 前进线速度 |
| Squeeze | 后退线速度 |
| Y 按钮 | 动作模式切换 |
| **手柄 6-DoF 姿态** | 双臂模式下控制左臂末端执行器 |

---

## 12. 关键文件清单

| 文件 | 行数 | 功能 |
|------|------|------|
| [teleop/TeleVision.py](teleop/TeleVision.py) | 328 | Vuer WebSocket 服务，`CONTROLLER_MOVE` 事件处理，左右手柄共享内存 |
| [teleop/VuerTeleop.py](teleop/VuerTeleop.py) | 116 | 封装层，`step()`/`step_both()` 姿态读取接口 |
| [teleop/Preprocessor.py](teleop/Preprocessor.py) | 40 | Y-up → Z-up 坐标变换 |
| [teleop/constants_vuer.py](teleop/constants_vuer.py) | 13 | `grd_yup2grd_zup` 变换矩阵 |
| [teleop/mapping/vr_mapper.py](teleop/mapping/vr_mapper.py) | 227 | VR → 机器人末端位姿映射，中性位姿校准 |
| [teleop/vr_arm_engine.py](teleop/vr_arm_engine.py) | 306 | 可复用的单臂 VR 引擎（状态机 + IK） |
| [teleop/app.py](teleop/app.py) | 356 | 独立遥操作初始化和主循环 |
| [teleop/runtime/init_right_controller.py](teleop/runtime/init_right_controller.py) | 239 | 右手柄状态解析（squeeze/trigger/A 键） |
| [teleop/runtime/init_left_controller.py](teleop/runtime/init_left_controller.py) | 95 | 左手柄状态解析（thumbstick/trigger/Vision60） |
| [teleop/runtime/context.py](teleop/runtime/context.py) | 110 | RuntimeContext 数据类 |
| [teleop/control/ik_stepper.py](teleop/control/ik_stepper.py) | 70 | MINK IK 单步求解 |
| [teleop/kinematics/pose.py](teleop/kinematics/pose.py) | 67 | pose7 → 4×4 矩阵，四元数符号稳定 |
| [teleop/motion_utils.py](teleop/motion_utils.py) | 15 | 矩阵更新和快速求逆辅助 |
| [lerobot_teleoperator_quest3_vr/quest3_vr.py](lerobot_teleoperator_quest3_vr/quest3_vr.py) | 173 | 单臂 LeRobot Teleoperator 插件 |
| [lerobot_teleoperator_bi_quest3_vr/bi_quest3_vr.py](lerobot_teleoperator_bi_quest3_vr/bi_quest3_vr.py) | 175 | 双臂 LeRobot Teleoperator 插件 |
| [lerobot_teleoperator_quest3_vr/config_quest3_vr.py](lerobot_teleoperator_quest3_vr/config_quest3_vr.py) | 31 | 单臂配置（draccus 注册） |
| [lerobot_teleoperator_bi_quest3_vr/config_bi_quest3_vr.py](lerobot_teleoperator_bi_quest3_vr/config_bi_quest3_vr.py) | 38 | 双臂配置（draccus 注册） |
| [LEROBOT_INTEGRATION.zh-CN.md](LEROBOT_INTEGRATION.zh-CN.md) | 313 | LeRobot 集成中文文档 |

---

## 13. 完整数据流管线图

```
┌─────────────────────────────────────┐
│         Quest3 VR App (WebXR)        │
│   ┌──────────┐    ┌──────────┐      │
│   │ 左手柄    │    │ 右手柄    │      │
│   │ 6-DoF +  │    │ 6-DoF +  │      │
│   │ 按键状态  │    │ 按键状态  │      │
│   └────┬─────┘    └────┬─────┘      │
└────────┼───────────────┼────────────┘
         │   WebSocket (HTTPS)   │
         ▼                       ▼
┌─────────────────────────────────────────────┐
│        Vuer Server (TeleVision.py)           │
│  ┌──────────────────────────────────────┐   │
│  │  on_controller_move()                │   │
│  │  • event.value["left"]  → left_shared │   │
│  │  • event.value["right"] → right_shared│   │
│  │  • event.value["leftState"]  → 14-d   │   │
│  │  • event.value["rightState"] → 14-d   │   │
│  └──────────────────────────────────────┘   │
│         共享内存 (multiprocessing.Array)       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│          VuerTeleop (VuerTeleop.py)          │
│  ┌──────────────────────────────────────┐   │
│  │  Preprocessor: Y-up → Z-up 变换       │   │
│  │  step()  → right_pose7 [x,y,z,q...]  │   │
│  │  step_both() → (left_pose7, right)   │   │
│  └──────────────────────────────────────┘   │
└──────────────────┬──────────────────────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
┌──────────────────┐ ┌──────────────────┐
│  LeftController   │ │ RightController  │
│  (left_controller │ │ (right_controller│
│   _init.py)       │ │  _init.py)       │
│                   │ │                  │
│  • thumbstick →   │ │  • squeeze ↔    │
│    Vision60 遥控   │ │    TELEOP/HOLD  │
│                   │ │  • A → RETURN   │
│                   │ │  • trigger →     │
│                   │ │    夹爪 EMA      │
└────────┬─────────┘ └────────┬─────────┘
         │                    │
         ▼                    ▼
┌──────────────────────────────────────────────┐
│          VRToRobotMapper (vr_mapper.py)        │
│                                                │
│  位置: scale × (vr_pos - neutral) + base_pos   │
│  旋转: base_R @ (P @ dR_vr @ P^T)             │
│  轴映射 P: X←-Z_vr, Y←Y_vr, Z←X_vr           │
└──────────────────┬───────────────────────────┘
                   │  target_T (4×4 末端目标位姿)
                   ▼
┌──────────────────────────────────────────────┐
│         MINK IK Solver (ik_stepper.py)         │
│                                                │
│  MuJoCo FK → 任务设置 → solve_ik → 积分更新    │
│  输出: [q1, q2, q3, q4, q5, q6] (6 轴关节角)  │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│     Piper 发送进程 (piper_send_process.py)     │
│                                                │
│  CAN 总线 → 真实 Piper 机械臂                  │
│  can_left  → 左臂 (双臂模式)                   │
│  can_right → 右臂 (单臂/双臂模式)              │
└──────────────────────────────────────────────┘
```

---

> **文档生成日期**: 2026-07-15
> **基于代码分支**: `piper-quest3-teleop-lerobot`
