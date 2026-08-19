# AI 资源中心 — UI 布局与代码结构说明

> V4 · 商业级 IDE 工作流布局 · 2026-07-29

---

## 1. 客观评估：为什么从 V3 切到 V4

V3 的 "顶栏类别 + 左侧窄卡片浏览器 + 右侧固定表单" 本质仍是**数据库后台**布局：
- 顶栏把类别横向铺开，浪费了宝贵的垂直空间；
- 左侧浏览器被锁死在 300–420px，视觉资产（人物/场景） thumbnails 只能很小；
- 右侧详情面板默认占半屏，即使没选中任何资产也赖在那里；
- 满眼都是 `QLineEdit` / `QFormLayout` 边框，视觉焦点涣散。

V4 把它重构成 **IDE 三列工作流**：左侧导航/筛选、中间沉浸式主画布、右侧抽屉式检查器。画布占据 60%–70%，资产真正成为视觉中心。

---

## 2. 全貌：无缝三列布局

```
┌─────────────────────────────────────────────────────────────────────┐
│  SidebarNav (200px) │ MainCanvas (stretch)        │ PropertyInspector│
│  BG_PANEL #18181A   │ BG_PAGE #121214             │ (320px,抽屉式)  │
│                     │                             │ BG_PANEL #18181A │
│  AI 资源中心        │  ┌ Action Bar (BG_PANEL)  ┐ │                  │
│  [人物]             │  │ 搜索框        [+新建]  │ │  检查器        │
│  [场景]             │  │ [复制][删除][检查器]   │ │  [编辑]  [›]   │
│  [Prompt]           │  └────────────────────────┘ │                  │
│                     │                             │  Hero 图/代码块 │
│  标签               │  人物/场景：                 │                  │
│  · 主角             │  ┌────┐┌────┐┌────┐       │  名称            │
│  · 写实             │  │ 140││ 140││ 140│       │  描述            │
│  · 都市             │  │x190││x190││x190│       │  ...             │
│                     │  └────┘└────┘└────┘       │                  │
│  平台 / Provider    │  Prompt：                    │  [保存修改]      │
│  ☑ Seedream         │  ┌─────────────────────┐   │                  │
│  ☑ Veo              │  │ 产品图   模板片段...│   │                  │
│  ☑ GPT Image        │  └─────────────────────┘   │                  │
│                     │                             │                  │
└─────────────────────┴─────────────────────────────┴──────────────────┘
```

### 设计层级（Z 轴用明度而非边框）

| 层级 | 颜色 | 用途 |
|------|------|------|
| 画布（最深） | `#121214` | 主画布背景，让视觉资产浮起来 |
| 面板（隆起） | `#18181a` | 侧边栏、检查器、Action Bar |
| 卡片 | `#1b1b1e` | ResourceCard / PromptRow |
| 输入框 | `#1a1a1d` | 编辑态输入框 |
| 输入框 hover | `#202024` | 编辑态 hover |
| 分割线 | `#232327` | 极少使用，仅 Action Bar 下边缘 |

### 强调色使用规则

- **紫色 `#b98cff`**：人物类别（左侧选中文字、卡片选中边框）
- **绿色 `#48d597`**：场景类别
- **橙色 `#ffa53d`**：Prompt 类别 / Prompt 变量 `{var}` 高亮
- **蓝色 `#3d8ef8`**：全局保存按钮、聚焦态边框、检查器开关（不随类别改变）

---

## 3. 组件树

```
ResourceCenterTab (QWidget, objectName=ResourceCenterRoot)
├── QHBoxLayout (spacing=0, margins=0)
│
├── SidebarNav (fixed 200px, BG_PANEL)
│   ├── QLabel "AI 资源中心"
│   ├── 类别按钮 × 3（人物/场景/Prompt）
│   ├── QLabel "标签"
│   ├── 标签按钮流（点击切换过滤）
│   ├── QLabel "平台 / Provider"
│   └── Provider 复选框（仅在 Prompt 类别显示）
│       └── Seedream / Veo / GPT Image / Kling / FLUX
│
├── MainCanvas (stretch=1, BG_PAGE)
│   ├── Action Bar (BG_PANEL, 底部 1px 分割线)
│   │   ├── QLineEdit 搜索框
│   │   ├── stretch
│   │   ├── QPushButton "＋ 新建"
│   │   ├── QPushButton "复制"
│   │   ├── QPushButton "删除"
│   │   └── QPushButton "检查器"（checkable）
│   │
│   └── QScrollArea (transparent)
│       ├── 人物/场景：QGridLayout + ResourceCard(140×190)
│       └── Prompt：QGridLayout + PromptRow（单行紧凑列表）
│
└── PropertyInspector (fixed 320px, BG_PANEL, 默认隐藏)
    ├── 标题栏
    │   ├── QLabel 类别·检查器
    │   ├── QPushButton "编辑"/"阅览"
    │   └── QPushButton "›" 折叠
    ├── QScrollArea
    │   ├── 人物/场景：Hero 图（280×180，点击 Lightbox）
    │   └── Prompt：代码块预览（`{var}` 橙色高亮）
    │   └── 表单字段（QLineEdit / QTextEdit / QSpinBox / QCheckBox）
    └── 保存栏（底部）
        └── QPushButton "保存修改"（#3d8ef8）
```

### 关键子组件

| 类 | 职责 |
|---|------|
| `SidebarNav` | 左侧导航 + 标签过滤 + Provider 过滤 |
| `MainCanvas` | 中间主画布：Action Bar + 自适应卡片/列表 |
| `ResourceCard` | 140×190 缩略图卡片，hover 发光、选中 accent 边框 |
| `PromptRow` | Prompt 列表行：分类徽章 + 名称 + 模板片段，左侧 accent 选中条 |
| `PropertyInspector` | 右侧抽屉式检查器：阅览/编辑双态、Hero 图、代码高亮 |
| `Lightbox` | 点击图片后的全屏沉浸式预览，点击/Esc 关闭 |

---

## 4. 三类资源的行为差异

| 类别 | 主题色 | 中间视图 | 右侧检查器 | 特有字段 |
|------|--------|---------|-----------|---------|
| **人物** | `#b98cff` | 140×190 缩略图网格 | Hero 图 + 名称/年龄/性别/描述/Seedream/Veo Prompt/参考图/embedding/tags | age, gender, embedding_path |
| **场景** | `#48d597` | 140×190 缩略图网格 | Hero 图 + 名称/描述/Seedream Prompt/参考图/tags | — |
| **Prompt** | `#ffa53d` | 紧凑列表视图 | 代码块预览（变量高亮）+ 名称/分类/适用平台/变量/模板/tags | provider, defaults |

---

## 5. 交互流程

### 5.1 切换类别
```
SidebarNav._set_cat(kind)
  → 更新左侧按钮样式（accent 色文字）
  → 隐藏/显示 Provider 过滤区（仅 Prompt 可见）
  → ResourceCenterTab._set_kind(kind)
      → MainCanvas.set_kind(kind, accent)
      → _reload_browser()  → 加载资产 + 收集标签
      → _canvas.set_tag_filter(set(), set())  → 清空过滤
      → 隐藏 Inspector
```

### 5.2 选中资产
```
ResourceCard.clicked.emit(id)  或  PromptRow.clicked.emit(id)
  → MainCanvas._on_pick(id)
      → 设置选中态（property + unpolish/polish）
      → itemSelected.emit(id)
          → ResourceCenterTab._on_pick(id)
              → 从 DB 读取完整 item
              → PropertyInspector.load(item, kind, accent)
                  → 显示 Inspector
                  → 渲染 Hero / 代码块
                  → 构建表单字段
                  → 默认进入阅览模式
```

### 5.3 阅览 / 编辑双态
```
初始：QLineEdit/QTextEdit/QSpinBox 只读、无边框、透明背景 → 看起来像纯文本
点击 [编辑]：可读写、边框出现、输入背景 #1a1a1d
点击 [阅览]：回到只读态
[保存修改] 仅在编辑态可用
```

### 5.4 保存
```
PropertyInspector._on_save()
  → 从 _fields 读取当前值
  → Prompt：保留原 defaults 中非 vars 字段，再用 vars 输入框覆盖 vars
  → 组装 dataclass，补 id / updated_at
  → AssetDB.save_* 入库
  → saved.emit(item)
      → ResourceCenterTab._on_saved(item)
          → _reload_browser() + mark_selected(item.id)
```

### 5.5 搜索与过滤
```
搜索框 textChanged
  → MainCanvas._apply_filter()
      → 按 name + tags + template 文本匹配

标签按钮点击 / Provider 复选框切换
  → SidebarNav.filterChanged.emit()
      → ResourceCenterTab._on_filter()
          → MainCanvas.set_tag_filter(tags, providers)
              → 重新过滤并渲染
```

### 5.6 键盘快捷流
```
Space（不在输入框内）：选中人物/场景且有参考图时打开 Lightbox
Ctrl+C（不在输入框内）：深拷贝当前选中资产到内部剪贴板
Ctrl+V（不在输入框内）：从剪贴板粘贴为新建资产（自动重命名加“副本”）
```

---

## 6. 文件结构与代码映射

```
ai/
├── assets/
│   └── db.py            → AssetDB + 数据模型；_save 自动为空 id 补 uuid
├── ui/
│   └── resource_center.py  → 本文件（V4）
└── service.py           → get_asset_db() 全局单例
```

### resource_center.py 内部函数/类

| 名称 | 职责 |
|------|------|
| `_cover` / `_fit` / `_thumb` | 图像裁剪、等比缩放、占位图 |
| `_highlight_prompt` | 将 `{var}` 渲染为橙色 HTML 高亮 |
| `ElideLabel` | 自动省略号标签 |
| `Lightbox` | 全屏图片预览 |
| `SidebarNav` | 左侧导航/筛选栏 |
| `ResourceCard` | 人物/场景卡片（140×190） |
| `PromptRow` | Prompt 列表行 |
| `MainCanvas` | 中间主画布 |
| `PropertyInspector` | 右侧属性检查器 |
| `ResourceCenterTab` | 顶层容器、交互编排、键盘事件 |

---

## 7. 关键设计决策

1. **无缝三列**：`QHBoxLayout(spacing=0, margins=0)`，用 `#18181a` vs `#121214` 的明度差自然划分区域，不用厚重边框。
2. **类别从顶栏移入左侧导航**：释放顶部空间，下方可容纳标签树和 Provider 过滤。
3. **画布主导**：`MainCanvas` 占 `stretch=1`，卡片放大到 140×190，缩略图更大、更易辨认。
4. **Prompt 用列表视图**：文本模板不需要方形封面，列表更紧凑、信息密度更高。
5. **检查器抽屉式**：默认隐藏；选中资产时滑出；无选中时画布占满全部空间。
6. **阅览/编辑双态**：降低日常浏览时的视觉噪音；只有进入编辑态才出现输入框边框。
7. **保存按钮统一蓝色**：不使用类别 accent，避免界面躁动，保持专业感。
8. **Hero 图 + Lightbox**：人物/场景顶部铺满大图，点击弹出全屏查看；Prompt 顶部是带变量高亮的代码块。
9. **DB 层兜底**：`AssetDB._save` 对空 id 自动生成 uuid，避免新资产编辑时误生成重复行。
