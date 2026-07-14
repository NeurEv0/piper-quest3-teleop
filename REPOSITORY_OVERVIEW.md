# 仓库结构与内容概览

生成日期：2026-07-14  
仓库路径：`/home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop`  
当前分支：`piper-quest3-teleop-lerobot`  

## 1. 项目定位

这个仓库现在聚焦于 **Quest 3 / Vuer VR 遥操作 AgileX Piper 机械臂**。核心链路是：

1. 通过 Vuer 接收 VR 控制器状态与位姿。
2. 将右手控制器位姿转换到机器人末端执行器目标位姿。
3. 使用 MuJoCo + MINK 求解 Piper 机械臂逆运动学。
4. 将 6 轴关节角与夹爪命令通过 Piper SDK / CAN 发送给真实机械臂。
5. 可选地把 OpenCV 摄像头图像写入共享内存，在 Quest 3 端作为双目背景显示。

## 2. 顶层结构

```text
.
├── README.md
├── requirements.txt
├── LICENSE
├── ORIG_REPOSITORY_OVERVIEW.md
├── REPOSITORY_OVERVIEW.md
├── teleop/
├── scripts/
├── assets/
└── img/
```

| 路径 | 作用 |
| --- | --- |
| `teleop/` | 实时 VR 遥操作主程序，包含 Vuer 通信、Quest 控制器处理、IK、Piper 驱动、运行时状态机。 |
| `scripts/` | 数据后处理、仿真回放、策略部署、动作绘图等辅助脚本。 |
| `assets/` | Inspire hand / H1 Inspire 的 URDF、mesh、launch 文件，以及 demo GIF。 |
| `teleop/piper/agilex_piper/` | Piper 机械臂的 MuJoCo MJCF 模型和 mesh 资产。 |
| `img/` | README 或项目展示用图片。 |
| `requirements.txt` | Python 依赖列表。 |
| `LICENSE` | Apache License 2.0，仍保留上游项目致谢信息。 |
| `ORIG_REPOSITORY_OVERVIEW.md` | 清理前的仓库概览快照，保留作对照。 |
| `REPOSITORY_OVERVIEW.md` | 清理后的当前概览，也就是本文档。 |

## 4. 规模概况

清理后主要文件类型数量：

| 类型 | 数量 | 说明 |
| --- | ---: | --- |
| `.py` | 47 | Python 主代码和脚本。 |
| `.STL` | 112 | 机器人/手部大写 STL mesh。 |
| `.stl` | 75 | 机器人/手部小写 STL mesh。 |
| `.obj` | 72 | Piper MJCF 模型 mesh。 |
| `.md` | 5 | 项目文档与子模块说明。 |
| `.urdf` | 5 | H1 Inspire / Inspire hand 机器人描述。 |
| `.xml` | 3 | MuJoCo MJCF 模型与场景。 |

## 5. 运行入口与参数

### 实时遥操作入口

主入口文件是：

```text
teleop/teleop_real_arm.py
```

推荐以模块方式运行：

```bash
python -m teleop.teleop_real_arm
```

可用参数定义在 `teleop/cli.py`：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--can` | `can0` | Piper 机械臂 CAN 端口。 |
| `--dry-run` | `False` | 不发送真实硬件命令；会启动 MuJoCo viewer。 |
| `--config` | `inspire_hand.yml` | 配置文件名；当前主链路中使用较少。 |
| `--camera` | `None` | OpenCV 摄像头编号；不传则禁用摄像头。 |
| `--print-freq` | `False` | 预留/调试用参数。 |
| `--debug-mapper` | `False` | 打印 VR 到机器人位姿映射调试信息。 |

示例：

```bash
python -m teleop.teleop_real_arm --dry-run --debug-mapper
python -m teleop.teleop_real_arm --can can0 --camera 0
```

真实硬件运行依赖 CAN、Piper SDK、Quest/Vuer HTTPS 证书和机器人安全状态。`--dry-run` 更适合先验证 MuJoCo/MINK 和 VR 数据流。

## 6. Teleop 主链路

主逻辑分为两个阶段：

1. `build_runtime(args)` 初始化运行时对象。
2. `run_loop(args, rt)` 进入实时控制循环。

`teleop/app.py` 的 `build_runtime()` 会创建：

| 组件 | 来源 | 作用 |
| --- | --- | --- |
| `VuerTeleop` | `teleop/VuerTeleop.py` | 创建共享图像缓冲区，启动 Vuer 服务，读取 Quest 控制器位姿。 |
| `LeftController` | `teleop/runtime/init_left_controller.py` | 处理左手控制器，当前用于 Vision60 移动控制桥接。 |
| `RightController` | `teleop/runtime/init_right_controller.py` | 处理右手 squeeze、return-to-zero、trigger 夹爪输入。 |
| `OpenCVCameraStreamer` | `teleop/io/camera.py` | 可选摄像头输入，写入双目共享图像缓冲区。 |
| `PiperForwardKinematics` | `teleop/kinematics/piper_forward_kinematics.py` | Piper 正运动学，计算零位末端位姿和骨架显示点。 |
| `VRToRobotMapper` | `teleop/mapping/vr_mapper.py` | 将 VR 控制器相对运动映射成机器人末端目标位姿。 |
| MuJoCo/MINK | `teleop/runtime/init_mink.py` | 加载 `piper.xml`，构造 IK task、约束、求解器和 rate limiter。 |
| Piper 发送进程 | `teleop/runtime/piper_send_process.py` | 独立进程定频读取共享命令并发送到硬件。 |

`run_loop()` 中的核心状态如下：

| 状态 | 含义 |
| --- | --- |
| `RETURNING` | 回到机械臂零位附近。 |
| `AT_ZERO` | 已在零位，等待右手 squeeze 进入遥操作。 |
| `TELEOP` | 右手 squeeze 按住时，控制器运动映射到末端目标并运行 IK。 |
| `HOLD` | 松开 squeeze 后保持当前位姿；再次 squeeze 可继续；A 键触发回零。 |

循环内的主要顺序：

1. 检查 viewer 是否仍在运行。
2. 启动阶段先写入全零关节命令。
3. 从 Vuer 读取右手控制器位姿。
4. 更新左右控制器状态。
5. 根据状态机选择目标末端位姿。
6. 使用 MINK 求解 IK，更新 6 轴关节角和 MuJoCo gripper qpos。
7. TELEOP 状态下把 FK 骨架发送给 Vuer 显示。
8. 写入共享命令数组，由子进程发送给 Piper。
9. 可选更新摄像头帧和 MuJoCo viewer。

## 7. `teleop/` 目录详解

```text
teleop/
├── teleop_real_arm.py
├── app.py
├── cli.py
├── config.py
├── VuerTeleop.py
├── TeleVision.py
├── Preprocessor.py
├── mapping/
├── control/
├── runtime/
├── piper/
├── kinematics/
├── io/
├── utils/
└── piper/agilex_piper/
```

| 文件/目录 | 内容 |
| --- | --- |
| `teleop/teleop_real_arm.py` | 主程序入口，负责 parse args、build runtime、run loop 和退出时安全处理。 |
| `teleop/app.py` | 遥操作系统的初始化和主循环。 |
| `teleop/cli.py` | 命令行参数定义。 |
| `teleop/config.py` | UDP、Piper 单位换算、MINK 参数、gripper 映射、MJCF 路径等配置。 |
| `teleop/VuerTeleop.py` | 创建共享图像数组，启动 `OpenTeleVision`，将右手控制器矩阵转为 `[x,y,z,qx,qy,qz,qw]`。 |
| `teleop/TeleVision.py` | Vuer 服务封装，处理 `CONTROLLER_MOVE` 事件，维护左右手状态、右手 4x4 矩阵、共享图像和机器人骨架显示。 |
| `teleop/Preprocessor.py` | 将 Vuer 的 Y-up 坐标系转换为控制侧使用的 Z-up 坐标系。 |
| `teleop/mapping/vr_mapper.py` | VR 到机器人末端位姿映射和中立位姿校准。 |
| `teleop/runtime/` | 初始化、状态上下文、控制器、发送进程、ROS2 bridge 预留代码。 |
| `teleop/control/` | IK stepper、Piper command sender、旋转平滑、夹爪工具。 |
| `teleop/piper/` | Piper SDK 驱动封装、安全辅助函数、Piper MJCF 模型。 |
| `teleop/kinematics/` | Piper 正运动学与 pose 转换工具。 |
| `teleop/io/` | OpenCV 摄像头采集与共享图像写入。 |
| `teleop/utils/` | profiling、回零同步、单位转换等工具。 |

## 8. `scripts/` 目录详解

| 文件 | 作用 |
| --- | --- |
| `scripts/post_process.py` | 读取 ZED `.svo` 和机器人 `.hdf5`，按时间戳对齐图像、状态、动作，生成 processed HDF5。 |
| `scripts/replay_demo.py` | 使用 Isaac Gym 加载 `assets/h1_inspire/urdf/h1_inspire.urdf`，回放 processed episode 的动作和双目图像。 |
| `scripts/deploy_sim.py` | 加载 JIT policy 和归一化统计，在仿真回放环境中执行策略输出。 |
| `scripts/plot_action.py` | 动作数据绘图脚本。 |

这些脚本仍依赖仓库外部的 `data/` 目录、Isaac Gym、ZED SDK / `pyzed`、CUDA 等环境，不能只靠 `requirements.txt` 完整复现。

## 9. `assets/` 与模型资产

```text
assets/
├── demo.gif
├── inspire_hand/
└── h1_inspire/
```

| 路径 | 内容 |
| --- | --- |
| `assets/demo.gif` | README 使用的演示 GIF。 |
| `assets/inspire_hand/` | Inspire hand 左右手 URDF 和 mesh。 |
| `assets/h1_inspire/` | H1 Inspire 机器人包，包含 URDF、launch、RViz 配置、Gazebo launch 和大量 mesh。 |

Piper 机械臂的 MuJoCo 模型放在：

```text
teleop/piper/agilex_piper/
├── piper.xml
├── scene.xml
├── assets/
├── README.md
├── CHANGELOG.md
└── LICENSE
```

`teleop/config.py` 中的 `PIPER_MJCF_PATH` 指向 `teleop/piper/agilex_piper/piper.xml`。

## 10. 依赖概览

`requirements.txt` 仍保留训练、视觉和机器人相关依赖。
| 类别 | 代表依赖 |
| --- | --- |
| VR / WebRTC / Vuer | `vuer`, `aiohttp`, `aiohttp_cors`, `aiortc`, `av` |
| 机器人与控制 | `mink`, `mujoco`, `piper-sdk`, `python-can`, `loop-rate-limiters`, `dynamixel_sdk` |
| 数值计算 | `numpy`, `scipy`, `pytransform3d`, `scikit_learn`, `pandas` |
| 视觉与数据 | `opencv_python`, `opencv_contrib_python`, `h5py`, `matplotlib`, `seaborn` |
| 策略部署/回放 | `torch`, `torchvision`, `einops`, `tqdm` |

代码中还出现 `isaacgym`、`pyzed.sl`、`rclpy` 等依赖，它们没有列在 `requirements.txt` 中，通常需要单独安装或由特定环境提供。

## 12. 仍需后续确认的点

1. `teleop/test_vuer_teleop.py` 中调用 `VuerTeleop("inspire_hand.yml")`，但当前 `VuerTeleop.__init__()` 不接收参数；该测试文件可能已经过期。
2. `teleop/teleop_real_arm.py` 退出阶段尝试访问 `rt.driver`，但当前 `RuntimeContext` 没有 `driver` 字段；真实硬件退出时的 safety 回零可能需要复查。
3. `teleop/config.py` 定义了 `GRIPPER_MAX_UM`，而 `teleop/control/sender.py` 读取的是 `GRIP_MAX_UM`；因为使用了默认值，运行不一定报错，但命名可能不是同一套配置。
4. `teleop/runtime/init_ros2_bridge.py` 存在，但主循环中相关初始化被注释，Vision60 bridge 当前默认不启用。
5. `scripts/` 默认使用 `../data/recordings`、`../data/logs` 等路径；当前仓库未包含数据目录。

## 13. 快速阅读建议

如果要继续理解或修改实时遥操作主链路，可以按以下顺序读代码：

1. `teleop/teleop_real_arm.py`
2. `teleop/app.py`
3. `teleop/VuerTeleop.py`
4. `teleop/TeleVision.py`
5. `teleop/mapping/vr_mapper.py`
6. `teleop/runtime/init_mink.py`
7. `teleop/control/ik_stepper.py`
8. `teleop/runtime/piper_send_process.py`
9. `teleop/piper/driver.py`
10. `teleop/runtime/context.py`

这条路径基本覆盖了从 Quest 控制器输入到 Piper 关节命令输出的主链路。
