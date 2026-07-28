# AI 素材批处理工作台（AI Asset Studio）— 架构与实施规划

> 状态：规划文档 v1（2026-07-24），尚未写代码。
> 定位：把"批量 AI 去水印"作为**其中一个功能**，工作台还包含超分 / 去 Logo / 去文字 / 修复 / 改比例 / 重命名 / 格式转换 / 压缩。
> 决策原则：**先做能发货的（固定蒙版 + onnx CPU，进打包 exe），把"真扩散 AI"设计成可选 Pro 引擎插件。**

---

## 1. 可行性结论速览

| 方案 | 可行性 | 进打包 exe | 硬件 | 阶段 |
|---|---|---|---|---|
| 固定区域蒙版 + `cv2.inpaint` | ✅ 立即可做 | ✅ | 任意 CPU | 首版 |
| 复用现有 onnx 超分/抠图进流水线 | ✅ 已有 | ✅ | CPU | 首版 |
| 自动检测文字/Logo（YOLO + DINO + SAM，onnx 版） | ⚠️ 需补模型 | 部分可 | CPU/GPU | 第二阶段 |
| FLUX.1 Kontext dev（fp16 ~12–24GB） | ⚠️ 仅本机 CUDA 独显 | ❌ 不能打包 | NVIDIA ≥16GB | 选装 Pro |
| Qwen Image Edit（Apache2，可商用） | ⚠️ 同 Flux | ❌ 不能打包 | NVIDIA ≥16GB | 选装 Pro（长期优选） |

**关键约束（来自现有代码，必须遵守）：**
- 现有 AI 全部走「干净子进程 + onnxruntime CPU」（`_run_onnx_worker` / `_RembgWorker` / `_AIEnhanceWorker`），刻意规避 PyQt6/cv2 加载后的 onnxruntime 原生 DLL 冲突，并保证 PyInstaller 单文件 exe 可打包。
- FLUX/Qwen 是 PyTorch + CUDA 重模型，**进程内会与现有 PyQt6/cv2/onnxruntime 冲突，且体积无法打进单文件 exe**。只能作为"本机有显卡时单独加载"的 Pro 引擎，通过 `inference/` 接口接入，默认不启用。

---

## 2. 分层架构

```
UI（PyQt6，新模块 asset_studio）
        ↓
Task Queue（Producer 读 → Queue → Consumer 推理 → Saver 存）
        ↓
Inference Engine（BaseEngine 接口；onnx 子进程 / 可选 torch 引擎）
        ↓
Model（~/.cep_models/ 统一管理；Pro 引擎另指本地模型目录）
```

设计目标：**UI 不感知底层模型**。统一调用 `engine.process(image, mask) -> image`，切换/新增模型不动 UI。

---

## 3. 目录结构（融入现有仓库，尽量不动既有文件）

```
CreativeEnginePro/
├── ui/
│   ├── image_editor.py          # 现有单图编辑器（保持不变）
│   └── batch_workspace.py       # 新增：批量工作台 Widget（拖文件夹 / Pipeline / 队列 / 状态），作为「图片部分」下的独立左侧 Tab
├── core/
│   ├── asset_pipeline.py        # 新增：插件注册表 + BatchProcessor（三线程队列）
│   └── plugins/                 # 新增：各处理步骤（可自由组合）
│       ├── __init__.py
│       ├── watermark_fixed.py   # 固定区域蒙版 + cv2.inpaint
│       ├── watermark_auto.py    # 占位：自动检测（YOLO/DINO/SAM）第二阶段
│       ├── superres.py          # 接现有 realesr onnx 子进程
│       ├── denoise.py           # 可选
│       ├── resize.py            # 改比例 1:1 / 9:16 / 16:9
│       ├── rename.py            # 批量重命名
│       ├── convert.py           # 格式转换 png/jpg/jpeg/webp
│       └── compress.py          # 批量压缩
├── inference/                   # 新增：可选 Pro 引擎（torch，需本机 CUDA）
│   ├── __init__.py
│   ├── base_engine.py           # BaseEngine.process(image, mask) 抽象接口
│   ├── flux_engine.py           # FLUX.1 Kontext dev 加载一次常驻
│   └── qwen_engine.py           # Qwen Image Edit（长期优选）
├── utils/
│   └── mask.py                  # 新增：固定蒙版生成（参数化右下角矩形）+ 以后接检测器
└── main.py / ui/main_window.py  # 需在 main_window.py 左侧「图片部分」分组下新增一个独立 Tab（index 8）承载 BatchWorkspace
```

**集成方式（独立左侧 Tab，与「图片处理」并列）：** 在 `ui/main_window.py` 左侧「图片部分」分组（`SidebarGroup("图片部分")`）下，于「图片处理」按钮之后新增一个独立导航按钮「批量AI处理」，对应 `QStackedWidget` 索引 8，承载 `BatchWorkspace`（`ui/batch_workspace.py`）。

- `BatchWorkspace` 是一个独立 `QWidget`，不侵入现有单图编辑器（`ImageEditorContainer` 保持原样）；
- 通过 `SidebarGroup.add_button("批量AI处理", 8)` + 注册到 `self._tab_index` / `nav_btns` 接入现有的 `switch_tab` 机制；
- 复用仓库既有的 AI 模型下载体系（`~/.cep_models/` 按需下载，不打包进 exe）。

这样功能**位于图片部分、且在「图片处理」下方单独成 Tab**，既不遮挡单图编辑，又明确了「批量处理」与「单图编辑」的边界。

---

## 4. 插件化 Pipeline 设计（核心卖点）

处理步骤即插件，按顺序串联，UI 可勾选/排序：

```
Pipeline
  ↓ ① 去水印（固定蒙版 / 自动检测 / Pro 引擎）
  ↓ ② 超分（realesr onnx）
  ↓ ③ 去噪（可选）
  ↓ ④ 改比例（1:1 / 9:16 / 16:9）
  ↓ ⑤ 自动裁剪（可选）
  ↓ ⑥ 导出（格式 / 压缩 / 重命名）
```

- 每个插件实现统一接口：`run(image, ctx) -> image`，`ctx` 携带 mask / 参数 / 日志。
- UI 列表可拖拽排序、单独启停；新增功能只加一个插件文件，UI 零改动。
- 复用现有 `~/.cep_models/` 下载体系（`_AI_MODELS` / `_download_model` / `_model_local_path` / `_model_exists`）与模型管理对话框。

---

## 5. 任务队列与并发模型

按规格的 Producer / Consumer / IO 三线程，GPU（Pro 引擎）永不空转：

```
Producer 线程：递归扫描输入目录，把图片路径推入 Queue（不预加载像素）
   ↓ Queue（有界，防止内存爆）
Consumer 线程：取一张 → 跑 Pipeline（onnx 子进程 or Pro 引擎）→ 出结果数组
   ↓
Saver 线程：把结果写盘（保持子目录结构），更新进度/状态
```

- 内存约束：始终"读一张→推理→存→释放"，不整批加载。目标 CPU ≈ 数百 MB。
- 状态面板：进度条 `██ 432/1000` + GPU 显存 + CPU% + 速度(张/s) + 预计剩余时间 + 每项状态（等待/处理中/完成/失败）。
- 失败重试：记录失败清单与原因，支持"仅重试失败项"；导出 `process_log.csv`。

---

## 6. Mask 生成策略

**首版（覆盖用户现有素材）：固定区域蒙版。**
用户图片统一 `1024×1024`，水印固定在右下角 `280×80`，对应：
```python
mask = np.zeros((H, W), np.uint8)
mask[-90:-10, -310:-10] = 255   # 参数化：右下角上移/左移边距 + 宽高可调
```
无需 AI，速度比扩散模型快几十倍，且结果可控。UI 提供"区域预设 + 手动矩形选取"两种设置方式（手动选取复用 `image_editor` 已有矩形选区逻辑）。

**第二阶段：自动检测。** 水印位置不固定时，接入 YOLO / GroundingDINO / SAM2 的 onnx 版自动生成 mask，逻辑放在 `utils/mask.py` 与 `plugins/watermark_auto.py`，对 UI 透明。

---

## 7. GPU / 模型管理

- **onnx CPU 路径（首版默认）：** 无 GPU 占用，模型走 `~/.cep_models/`，子进程隔离推理，天然适配现有打包方式。
- **Pro 引擎（可选，需本机 CUDA）：** 软件启动时按用户配置**加载一次并常驻**，处理上万张不重复加载/释放；统一 `BaseEngine.process(image, mask)` 接口。仅当用户在设置里启用且本机检测到兼容 CUDA 环境时才初始化，否则工作台自动降级到固定蒙版/onnx 路径。
- 模型不放进 exe；Pro 引擎的模型目录由用户在设置中指定（HuggingFace 下载到本地）。

---

## 8. 批处理与目录结构保持

- 支持 `png / jpg / jpeg / webp`，递归扫描。
- 输入 `素材/A/`、`素材/B/` → 输出 `output/A/`、`output/B/`，目录树一致。
- 重命名/格式/压缩规则在导出插件中配置。

---

## 9. 分阶段路线图（落到具体文件）

### 第一阶段（首版，进打包 exe，无需 GPU）
- ✅ `ui/batch_workspace.py`（「图片部分」下独立左侧 Tab）：拖拽文件夹、Pipeline 勾选/排序、开始/暂停、进度与状态面板。
- ✅ `core/asset_pipeline.py`：三线程 BatchProcessor + 插件注册表。
- ✅ `core/plugins/watermark_fixed.py` + `utils/mask.py`：固定区域蒙版去水印。
- ✅ `core/plugins/resize.py` / `rename.py` / `convert.py` / `compress.py`：纯 Pillow/opencv 步骤。
- ✅ `core/plugins/superres.py`：接入现有 realesr onnx 子进程。

### 第二阶段（多线程增强 + 失败重试 + 自动蒙版雏形）
- ✅ 导出日志 / 失败重试 / ETA 精确化。
- ✅ 自动识别图片尺寸并适配蒙版参数。
- ✅ `watermark_auto.py`：轻量 onnx 检测器（文字/Logo）生成 mask。

### 第三阶段（真 AI + 插件扩展）
- ✅ `inference/base_engine.py` + `flux_engine.py` / `qwen_engine.py`：可选 Pro 引擎（需本机 CUDA）。
- ✅ 自动检测文字/Logo → AI 修复（任意位置）。
- ✅ 扩图 / 尺寸转换等更多插件。

### 第四阶段（商业版）
- ✅ ONNX / TensorRT 加速（支持者）。
- ✅ 多 GPU 调度。
- ✅ CLI 批处理。
- ✅ Windows 一键安装包（沿用现有 PyInstaller 单文件 + onnx subprocess 配方；Pro 引擎不打包，运行时按需下载）。
- ✅ 模型更新检查 / 配置管理。

---

## 10. 与现有 image_editor 的复用点

- `_run_onnx_worker` / `_RembgWorker` / `_AIEnhanceWorker`：onnx 子进程推理范式，直接复用。
- `~/.cep_models/` + 模型下载/管理对话框：Pro 与 onnx 模型统一来源。
- 矩形/椭圆/套索选区 + `Tool.HEAL` / `Tool.CLONE` + `layer.mask`：手动蒙版选取与单图修复逻辑可迁移到 `watermark_fixed` 的"手动矩形"模式。
- **批量工作台作为「图片部分」分组下的独立左侧 Tab（index 8）**，与「图片处理」并列，不侵入单图编辑器，避免遮挡现有图片编辑功能。

---

## 11. 打包与部署约束（务必遵守）

- 维持现有 PyInstaller 单文件 + `ffmpeg.exe` 内嵌 `_MEIPASS` 的配方。
- **onnxruntime 必须走子进程**（现有 hook/onnxruntime_runtime.py 已有 DLL 隔离方案），不要尝试进程内 import。
- **Flux/Qwen（torch）绝不打进 exe**；仅作为运行时可选依赖，由用户本机环境提供。
- 任何新增原生 DLL（如未来 TensorRT）都按"子进程 or 独立加载目录"处理，避免污染主进程加载器。

---

## 12. 未决项 / 第一步

1. **硬件确认（阻塞 Pro 引擎路线）：** 在本机命令行运行：
   ```bat
   nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
   ```
   - 有输出 → 记下列出的 `name` 与 `memory.total`（显存），据此判断能否跑 Flux（≥16GB 推荐）/ Qwen。
   - 报错"不是内部或外部命令" → 无 NVIDIA 独显驱动，只能走固定蒙版 + onnx CPU。
   - 想看集显/其他显卡：`wmic path win32_VideoController get name`。
   - 已装 PyTorch 可补一句：`python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no-cuda')"`。
2. 首版是否要把"自动检测蒙版"也纳入（影响 `watermark_auto` 是否第一阶段就占位）。
3. Pro 引擎默认用 Flux 还是 Qwen（长期商用倾向 Qwen Apache2）。
