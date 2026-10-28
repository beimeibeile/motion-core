# 三项目集成指南

omotion-core 作为 `.motion` 领域的共享能力底座，同时服务三个项目。每个项目采用最适合自身架构的集成方式，**均为一层依赖，无多层嵌套**。

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    应用层（3个项目）                       │
│  ┌────────────┐  ┌──────────────────┐  ┌────────────┐  │
│  │  AI Editor │  │  ai-video-editor │  │   O.motion │  │
│  │  (总指挥层) │  │  (能力注册中心)   │  │ (.motion专精)│  │
│  └─────┬──────┘  └────────┬─────────┘  └─────┬──────┘  │
│        │ SKILL.md 路由     │ 自动发现+invoke   │ 直接import│
├────────┼───────────────────┼──────────────────┼─────────┤
│        │        共享实现层（唯一 .motion 实现）  │         │
│  ┌─────▼───────────────────▼──────────────────▼─────┐   │
│  │              omotion-core                         │   │
│  │  SKILL.md(能力声明) + core/invoke.py(统一入口)     │   │
│  │  template_parser | renderer | quality_gate        │   │
│  │  param_manager | fragment_manager | utils         │   │
│  └───────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## 一、AI Editor 集成

**项目路径**：AI Editor 项目根目录
**架构特点**：总指挥/协调层，通过 SKILL.md 路由到 Driver Skill，不直接调用工具。

### 集成方式：SKILL.md 路由 + 直接调用

AI Editor 的 adu-driver 目前是完整的 Driver Skill（SKILL.md + 脚本）。集成 omotion-core 后：

1. **adu-driver 的实现逻辑逐步迁移到 omotion-core**
   - 渲染管线（motiongit_runner.py）→ `core/renderer/`
   - 模板管理（registry.json + 下载脚本）→ `core/template_parser/` + `core/param_manager/`
   - 质量检查 → `core/quality_gate/`

2. **adu-driver 保留为文档壳**
   - SKILL.md 保留，作为 AI Editor 路由的目标
   - SKILL.md 内容改为指引 agent 使用 omotion-core 的接口
   - 内部脚本逐步删除，改为调用 omotion-core

3. **AI Editor SKILL.md 更新**
   - 第二层驱动层清单中，adu-driver 的描述更新为"基于 omotion-core 的 .motion 渲染能力"

### 调用示例

```python
# AI Editor 项目中，agent 按 adu-driver SKILL.md 指引操作
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Motion-Core"))

from core.renderer import RenderPipeline
from core.quality_gate import QualityGate

pipeline = RenderPipeline("template.motion")
result = pipeline.render(params={"title": "Hello"}, output_path="out.mp4")
```

### 迁移路径

| 阶段 | 动作 |
|------|------|
| Phase 0 | omotion-core 骨架就绪，adu-driver 保持不变 |
| Phase 1 | adu-driver 渲染逻辑迁移到 omotion-core，adu-driver 改为调用核心库 |
| Phase 2 | adu-driver 脚本全部迁移，仅保留 SKILL.md 文档壳 |
| Phase 3 | adu-driver 标记为 deprecated，AI Editor 直接路由到 omotion-core |

---

## 二、ai-video-editor 集成

**项目路径**：ai-video-editor skill 目录（能力注册中心自动扫描 `.user_skills/` 定位）
**架构特点**：能力注册中心 v2.0，自动发现已安装 skill 的能力声明，通过 `invoke()` 统一调用，不直接 import。

### 集成方式：能力注册中心自动发现

omotion-core 的 `SKILL.md` 已包含 `metadata.capabilities` 声明，ai-video-editor 的 `CapabilityRegistry` 启动时自动扫描 `.user_skills/` 目录发现它。

**前提**：omotion-core 需要安装/链接到 ai-video-editor 可发现的 skill 目录。

### 安装方式

```powershell
# 方式一：创建符号链接（推荐，代码实时同步）
# 将 <motion_core_root> 替换为 motion-core 仓库的实际路径
New-Item -ItemType SymbolicLink `
  -Path "<user_skills_dir>\omotion-core" `
  -Target "<motion_core_root>"

# 方式二：复制目录（不推荐，更新需手动同步）
# Copy-Item -Recurse "<motion_core_root>" "<user_skills_dir>\omotion-core"
```

### 调用示例

```python
# ai-video-editor 内部，通过能力注册中心调用
from adapters.capability_registry import CapabilityRegistry

registry = CapabilityRegistry()

# 检查能力是否可用
if registry.is_available("motion_template_render"):
    result = registry.invoke("motion_template_render", {
        "template_path": "template.motion",
        "params": {"title": "Hello"},
        "output_path": "out.mp4"
    })

# 列出所有 omotion-core 能力
caps = registry.list_capabilities(skill="omotion-core")
```

### 在 task_router 中注册新模式

在 `core/task_router.py` 中添加 .motion 相关模式：

```python
MODE_TRIGGERS = {
    # ... 现有模式 ...
    "motion_template": [
        ".motion模板", "用motion模板", "渲染motion模板",
        "motion素材", "动效模板"
    ],
}

def get_capabilities_for_mode(mode):
    mapping = {
        # ... 现有映射 ...
        "motion_template": [
            "motion_template_parse",
            "motion_template_render",
            "motion_quality_check",
        ],
    }
    return mapping.get(mode, [])
```

### 能力降级

omotion-core 不可用时（如未安装、依赖缺失），ai-video-editor 自动降级：
- `motion_template_render` 不可用 → 回退到剪映工程构建（cap_effect_library + cap_keyframe_engine）
- `motion_quality_check` 不可用 → 跳过自动质量检查，使用基础帧验证

---

## 三、O_Motion 集成

**项目路径**：O_Motion 项目根目录
**架构特点**：.motion 专精应用，直接使用核心库，叠加模板创作工具链和批量产出调度。

### 集成方式：直接 import

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Motion-Core"))

# 直接使用各模块
from core.template_parser import parse_template
from core.renderer import RenderPipeline
from core.quality_gate import QualityGate
from core.fragment_manager import FragmentLibrary
from core.param_manager import ParamManager

# 或使用统一 invoke 入口
from core.invoke import invoke
result = invoke("motion_template_render",
    template_path="t.motion",
    params={"title": "Hello"},
    output_path="out.mp4")
```

### O.motion 在核心库之上的扩展

| 扩展模块 | 功能 | 依赖核心库 |
|---------|------|-----------|
| 模板创作工具链 | 脚手架、实时预览、打包器 | template_parser, renderer |
| 批量产出调度 | 任务队列、智能选型、并行渲染 | renderer, quality_gate |
| 片段智能复用 | 片段提取、组合、匹配推荐 | fragment_manager |
| MotionGit 集成 | 发布工具、API 客户端 | template_parser |
| 模板库管理 | 本地模板库、版本管理 | template_parser, param_manager |

---

## 四、统一接口规范

所有项目调用 omotion-core 必须遵循以下规范：

### 能力调用统一返回格式

```python
{
    "success": bool,        # 是否成功
    "capability": str,      # 能力名称
    "result": Any,          # 成功时的结果
    "error": str,           # 失败时的错误信息
    "code": str             # 错误码（CAPABILITY_NOT_FOUND / EXECUTION_ERROR）
}
```

### 能力清单

| 能力名称 | 功能 | 优先级 | 依赖 |
|---------|------|--------|------|
| `motion_template_render` | 模板渲染为视频 | 10 | python, ffmpeg, playwright |
| `motion_template_parse` | 模板解析 | 9 | python |
| `motion_quality_check` | 四层质量门 | 8 | python |
| `motion_param_manage` | 参数管理 | 7 | python |
| `motion_fragment_manage` | 片段库管理 | 6 | python |

### 错误码

| 错误码 | 含义 |
|--------|------|
| `CAPABILITY_NOT_FOUND` | 能力名称不存在 |
| `EXECUTION_ERROR` | 能力执行异常 |
| `TEMPLATE_PARSE_ERROR` | 模板解析失败 |
| `RENDER_TIMEOUT` | 渲染超时 |
| `QUALITY_CHECK_FAILED` | 质量门未通过 |

---

## 五、避免重复造车规则

1. **先查核心库**：开发 .motion 相关功能前，先检查 omotion-core 是否已有实现
2. **通用能力下沉**：某个功能在两个以上项目中需要 → 下沉到 omotion-core
3. **接口统一**：所有项目调用 .motion 功能必须通过 omotion-core 接口，不直接操作底层
4. **Bug 修复共享**：在 omotion-core 中修复的 bug，所有项目自动受益
5. **新能力沉淀**：新项目开发的 .motion 新能力 → 沉淀回 omotion-core → 所有项目获得
6. **定期同步**：omotion-core 更新后，各项目及时升级版本（符号链接方式自动同步）
