# O.motion Core

> `.motion` 格式专精的共享能力底座 —— 模板解析、HTML Canvas 渲染、四层质量门、参数管理、动画片段库

O.motion Core 是面向 `.motion` 开放动画模板格式的 Python 共享核心库，为多个本地项目提供统一的 .motion 能力，避免重复造车。

**双重身份**：
1. **Python 库**：直接 `import core.xxx` 使用
2. **可发现 Skill**：ai-video-editor 能力注册中心自动发现，通过 `invoke()` 统一调用

## 核心能力

| 能力名称 | 模块 | 功能 | 优先级 |
|---------|------|------|--------|
| `motion_template_render` | `core/renderer/` | 模板渲染为视频（Playwright 抽帧 + FFmpeg 编码） | 10 |
| `motion_template_parse` | `core/template_parser/` | 解析 standard 和 personal-download-v1 两种格式 | 9 |
| `motion_quality_check` | `core/quality_gate/` | 四层质量门（静态/参数/渲染/成品） | 8 |
| `motion_param_manage` | `core/param_manager/` | JSON Schema 参数解析、预设管理 | 7 |
| `motion_fragment_manage` | `core/fragment_manager/` | 动画片段库管理、提取、组合 | 6 |

## 格式兼容

严格遵循 [motion-spec](https://github.com/motiontemplates/motion-spec)：
- 内容层：`0.1.0-draft.1`
- 运行时：`html-canvas/1`
- 分发层：`1.0.0-rc.1`（personal-download-v1，可选）

支持两种模板格式：
- **standard**：明文 JSON，阿杜早期个人项目规范
- **personal-download-v1**：AES-256-GCM 加密，MotionGit 平台分发格式

## 安装

```bash
# 在仓库根目录执行开发模式安装
pip install -e .

# 或在项目中引用（使用相对路径）
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "motion-core"))
```

## 快速开始

### 方式一：直接 import

```python
from core.template_parser import parse_template
from core.renderer import RenderPipeline
from core.quality_gate import QualityGate

# 解析模板
template = parse_template("template.motion")

# 质量门检查
gate = QualityGate()
report = gate.check(template_path="template.motion", params={"title": "Hello"})

# 渲染
pipeline = RenderPipeline("template.motion")
pipeline.render(params={"title": "Hello"}, output_path="output.mp4")
```

### 方式二：统一 invoke 入口

```python
from core.invoke import invoke

# 渲染
result = invoke("motion_template_render",
    template_path="template.motion",
    params={"title": "Hello"},
    output_path="output.mp4")

# 质量检查
report = invoke("motion_quality_check",
    template_path="template.motion",
    params={"title": "Hello"})
```

### 方式三：ai-video-editor 能力注册中心

```python
from adapters.capability_registry import CapabilityRegistry

registry = CapabilityRegistry()
result = registry.invoke("motion_template_render", {
    "template_path": "template.motion",
    "params": {"title": "Hello"},
    "output_path": "output.mp4"
})
```

## 项目结构

```
omotion/
├── SKILL.md                  # 能力声明 + 使用手册（能力注册中心自动发现）
├── core/
│   ├── __init__.py
│   ├── invoke.py             # 统一调用入口（能力注册中心兼容）
│   ├── template_parser/      # 模板解析器（standard + personal）
│   ├── renderer/             # 渲染引擎（Playwright + FFmpeg）
│   ├── quality_gate/         # 四层质量门
│   ├── param_manager/        # 参数管理（Schema + 预设）
│   ├── fragment_manager/     # 片段管理（库 + 提取 + 组合）
│   └── utils/                # 工具函数
├── specs/                    # 接口规范与数据格式
├── docs/
│   ├── architecture.md       # 架构设计
│   └── integration.md        # 三项目集成指南
├── cli/                      # 命令行工具
├── setup.py
├── LICENSE
└── README.md
```

## 服务的项目

| 项目 | 集成方式 | 用途 |
|------|---------|------|
| **O.motion** | 直接 import | .motion 专精应用（模板创作 + 批量产出） |
| **AI Editor** | SKILL.md 路由 | 总指挥层，.motion 渲染子系统 |
| **ai-video-editor** | 能力注册中心自动发现 | 综合视频编辑，.motion 素材/模板能力 |

**扩展原则**：任何新项目需要 .motion 能力时，直接引用本库，不重复开发。

## 统一返回格式

所有能力调用返回统一格式：

```python
{
    "success": bool,        # 是否成功
    "capability": str,      # 能力名称
    "result": Any,          # 成功时的结果
    "error": str,           # 失败时的错误信息
    "code": str             # 错误码
}
```

## 许可

MIT License
