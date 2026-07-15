# 播放器架构重构方案 — Decode / Prepare / Render 三阶段

> 背景：用户以"如果这是我的项目"视角给出 15 条重构建议。本方案是对其的校准与落地规划——保留对的、推迟过度设计的、驳回前提不成立的，并把"三阶段分离"落成可执行的分阶段计划。
> 原则：**先做能直接灭掉"卡顿/音频顿"投诉的，再谈上限优化。**

---

## 0. 目标与不做什么（Scope Guard）

**目标**
- 播放跨转场 / 截断处不再有"音频顿一下、画面卡一下"。
- 连续播放 60s 内存平稳（无持续 malloc / 碎片）。
- 多轨（主视频 + 多叠加 + 字幕 + 转场）拖动 / 播放流畅。

**明确不做（避免过度设计）**
| 用户条目 | 决定 | 理由 |
|---|---|---|
| #1 OpenGLWidget 替换 QLabel | **延后到 Phase 3** | QLabel 上传非当前瓶颈；瓶颈是主线程逐帧合成 + 解码。高风险大改写，待 Phase 0-2 完成、确需 60fps 全屏再评估 |
| #6 PTS 内部时间基 | **低优先** | 单次 seek 的"秒→最近帧取整"误差可忽略；真正漂移来自时钟（#7），非 seek 精度 |
| #8 自管 KeyFrame/PTS 索引层 | **撤回** | cv2 `POS_MSEC` seek 对预览够用；自建帧管理层成本极高、收益低 |
| #10 GPU 转场 | **过早优化** | 1080p 两帧 numpy mix ≈ 10–50µs，远小于解码 ms 级；转场仅占 <1s 总时长。真上 OpenGL（Phase 3）会自然免费解决 |
| #5 数百帧 PTS→HashMap | **改为小环** | 8MB×N 内存；scrub 场景按需解码足够，只做 ±几帧小环 |

---

## 1. 现状盘点（已具备 vs 缺口）

| 能力 | 现状 | 缺口 |
|---|---|---|
| 异步解码线程 + fetch 队列 | ✅ `_fetch_thread` + `_fetch_queue` | 仅按需逐 seek 解码，**无预读环** |
| 画布 QImage 复用 | ✅ `_alloc_canvas` 尺寸/背景不变复用 `_canvas_cache` | 帧 QImage 仍每帧 `.copy()`（`_numpy_to_qimage`） |
| Layer 快路径 | ✅ `_flush_frame` 复用 `_last_frame_image` 做字幕拖拽/选中 | 无系统化的静态层缓存，播放期整画布重绘 |
| 音频子进程隔离 | ✅ ffplay 子进程 | 视频与音频**两套独立时钟未锁**（顿挫根因） |
| Alpha FFmpeg 管道 | ✅ `utils/alpha_video.py` | — |
| Prepare 阶段 | ❌ 缺失 | 缩放/变换/静态层每帧重算 |
| RenderContext | ❌ 缺失 | `render_frame` 直接读 `self.tl` 等 |

**关键事实（已从代码确认）**
- `_tick_preview` 用 `time.perf_counter()` delta 推进 `_preview_current` → wall-clock 驱动，与 ffplay 音频时钟脱钩。
- 时间线播放走 `_refresh_timer`(8ms) → `_flush_frame`，`_current_sec` 由 wall-clock 推进。
- 帧转 QImage：`_numpy_to_qimage` 每帧 `.copy()` 新分配。
- 无 decode-ahead 环；无 RenderContext。

---

## 2. 目标架构

### 2.1 线程模型（务实版，非激进五线程）

| 线程 | 职责 | 当前 |
|---|---|---|
| **UI / 主线程** | 只负责 present（贴图 / 将来贴纹理）+ 交互 | 现有，但当前还承担合成 |
| **Decode 线程** | 解码 → 写入 FrameCache 小环；持有预读游标 | 现有 `_fetch_thread` |
| **Audio 进程** | ffplay 子进程播放，主线程用其启动时刻反推音频时钟 | 现有 |
| **Thumbnail 线程** | 轨道缩略图（已存在） | 现有 `ThumbnailWorker` |
| **Prepare（轻量，主线程预通行）** | 生成 RenderContext、失效 LayerCache、提交缩放任务 | **新增，但算活轻** |

> 说明：激进的"Render 独立线程"只在 Phase 3（OpenGL）才有意义。当前让合成留在主线程、但把**重活（解码、缩放、静态层）前置到 Prepare/Decode**，主线程合成就成了"取缓存 + draw"，足够轻。

### 2.2 核心数据结构

**FrameCache（解码预读小环）**
- key：`(clip_id, frame_idx)`，按时间排序的环形缓冲，容量 ±N 帧（默认 N=5，约 ±0.17s@30fps，可配到领先 0.5s 的窗口）。
- 复用 numpy 缓冲（对象池），出环时归还，不 malloc/free。
- 回拖播放头：命中环内帧直接取，不重新 decode。

**AudioClock（音频主时钟）**
- `audio_clock_sec = offset_sec + (monotonic_now - ffplay_start_monotonic) * rate`
- ffplay 启动成功时记录 `ffplay_start_monotonic` 与 `-ss` 的 `offset_sec`。
- 视频目标时间对齐它；ffplay 未启动/失败时回退 wall-clock 并打 warn。

**RenderContext（每帧只读取的快照）**
- 生成时机：seek / 播放推进跨越帧时，由主线程轻量构建。
- 内容：`当前生效 clip 列表`、`各 clip 所处关键帧段`、`生效字幕块`、`PiP 列表`、`相邻转场对 (A,B,alpha,tfn)`、预计算好的 `QTransform`（scale/pos/rotation）。
- `render_frame(ctx)` 只读 ctx，不反查 `self.tl`。

**LayerCache（静态层位图缓存）**
- key：`(layer_id, 尺寸, 时间窗)`，例如"无动画字幕 Hello 在 0~5s"。
- 失效规则：跨越关键帧段、属性变更、字幕文本/样式改变 → invalidate。
- 命中则直接贴缓存位图，不重画。

**ScaledAssetCache（缩放结果缓存）**
- key：`(clip_id, target_w, target_h)`。
- 首次 `scaled()` 后缓存 numpy 结果，之后直接贴（信息流 PiP/LOGO 多为静态，收益高）。

---

## 3. 分阶段实施

### Phase 0 — 单一主时钟（灭顿挫根因）【最高优先】
- 改 `_tick` / `_tick_preview`：不再用 `perf_counter()` delta 推视频位置。
- 新增 `audio_clock_sec()`：由 ffplay 启动时刻 + `-ss` 偏移 + 墙钟 elapsed 反推当前音频秒。
- 视频目标时间 = `audio_clock_sec()`；帧推进对齐它。
- decode 跟不上 → **重复上一帧**（#11），绝不让主线程阻塞等待。
- 跨转场 / 截断处：A 片段 freeze 帧缓存、B 复用已解码帧，按 alpha 混合（沿用现有 `_trans_cache` 思路）。
- **验收**：播放跨转场 / 截断处无音频顿挫、无画面停顿。

### Phase 1 — 解码预读 + 帧缓冲池
- Decode 线程持有"预读游标"：播放时领先当前 ~0.5s 解码入 FrameCache 小环。
- `_numpy_to_qimage` 去掉 `.copy()`：改用**持久 numpy 缓冲 + 复用 QImage**（#2/#14）。
- 确认 `_alloc_canvas` 画布复用已在位（现状 ✅）。
- **验收**：连续播放不掉帧；进程内存 60s 内平稳，无持续增长。

### Phase 2 — Prepare 阶段（静态层 / 缩放 / 变换预计算）
- 引入 `RenderContext` 生成（seek/推进时主线程轻量构建，重活外置）。
- `ScaledAssetCache`：PiP/LOGO 缩放结果缓存（#4）。
- `LayerCache`：无动画字幕、静态叠加层缓存位图，render 直接贴（#3 系统化）。
- 变换矩阵（`QTransform` scale/pos/rotation）随 RenderContext 预计算，render 只 `draw`（#9）。
- **验收**：多 PiP + 多字幕场景 CPU 下降、拖拽 / 播放流畅。

### Phase 3（可选，确认后再做）— OpenGL 渲染层
- `QOpenGLWidget` 替换 `QLabel`，Texture 复用（#1）。
- 此时 #10 转场自然成为 shader；#1 上传成本归零。
- **前提**：Phase 0–2 完成，且实测仍需更高帧率 / 全屏流畅度。

---

## 4. 验收标准（总）

1. 播放跨转场 / 截断**无顿挫**（A/V 锁生效）。
2. 连续播放 60s 内存平稳（帧缓冲池复用，无泄漏）。
3. 多轨（主 + 3 叠加 + 字幕 + 转场）拖动 / 播放流畅。
4. 回拖播放头 ±N 帧内**命中 FrameCache 小环**，不重新 decode。

---

## 5. 风险与回退

- **音频时钟反推**依赖 ffplay 启动成功；ffplay 失败 → 回退 wall-clock 并 `logging.warning`，不影响播放只损失 A/V 严格同步。
- **RenderContext 失效规则**必须严谨：关键帧段切换、属性面板变更、字幕增删改必须 invalidate 对应 LayerCache，否则会出现"改了不动"的幽灵 bug。
- **FrameCache 容量**需压测：N 过大吃内存、过小失去预读意义，默认 N=5，按机器内存可调。

---

## 6. 与用户 15 条建议的映射总表

| # | 建议 | 本方案处理 |
|---|---|---|
| 1 | 弃 QLabel 用 OpenGL | Phase 3（延后） |
| 2 | 不每帧建 QImage | Phase 1（去 .copy，缓冲池） |
| 3 | 不每帧重画整画布 | Phase 2（LayerCache 系统化） |
| 4 | 所有缩放缓存 | Phase 2（ScaledAssetCache） |
| 5 | 真正 Frame Cache | Phase 1（改为 ±N 小环） |
| 6 | 不用秒做 seek | 低优先（单次 seek 误差可忽略） |
| 7 | 音频时钟驱动 | **Phase 0（最高优先）** |
| 8 | 自管帧索引/PTS | 撤回（cv2 够用） |
| 9 | QPainter 内无计算 | Phase 2（变换矩阵预计算入 ctx） |
| 10 | GPU 转场 | 过早优化（Phase 3 自然解决） |
| 11 | 不阻塞等待解码 | Phase 0（重复上一帧） |
| 12 | RenderContext | Phase 2 |
| 13 | 播放永远领先 | Phase 1（decode-ahead 环） |
| 14 | 对象池 | Phase 1（帧缓冲/QImage 复用） |
| 15 | 五线程 | 务实版（Decode/Audio/Thumbnail/UI + 轻量 Prepare，Render 独立线程留待 Phase 3） |

---

## 7. 实施进度

- **Phase 0 ✅（2026-07-13）**：音频主时钟落地。`_AudioPlayerSD.audio_clock_sec()` 反推 + `PreviewPlayer.master_clock_sec()` + `timeline_widget._tick_play` 改用音频时钟驱动视频。编译通过 + 6 项单测。
- **Phase 1 ✅（2026-07-13）**：解码预读 + 帧缓冲池落地。
  - 帧缓冲池：`_numpy_to_qimage` 改按 `(w,h,ch)` 复用持久 numpy 缓冲 + QImage，去每帧 `.copy()`。
  - 帧缓存环：`_compute_payload(sec, write_pending)` 抽取；`_payload_ring`(key=round(sec,3), max=8)；`_flush_frame` 优先命中环帧。
  - decode-ahead：`seek` 播放中传 `ahead`（默认 1），`_fetch_frame` 领先解码窗口帧只入环（`write_pending=False`）；顺序读状态写受 `write_pending` 保护。
  - 编译通过 + `work_temp/test_frame_ring.py` 4 项逻辑单测全过。
- **Phase 2 ⬜ 待实施**：RenderContext + LayerCache 系统化 + ScaledAssetCache + 变换矩阵预计算（#3/#4/#9/#12）。
- **Phase 3 ⬜ 可选**：OpenGL 渲染层（确认需更高帧率/全屏流畅再做）。

