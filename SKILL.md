---
name: omotion-core
description: ".motion 格式共享核心库 —— 模板解析、HTML Canvas 渲染、四层质量门、参数管理、动画片段库。为 AI Editor、ai-video-editor、O.motion 等项目提供统一的 .motion 能力底座，避免重复造车。支持直接 import 和能力注册中心自动发现两种集成方式。"
version: 0.1.0
author: O.motion Project
license: MIT
repository: https://github.com/beimeibeile/omotion
capabilities:
  - name: motion_template_parse
    description: "解析 .motion 模板文件，支持 standard（明文）和 personal-download-v1（AES-256-GCM 加密）两种格式，输出模板元数据、场景列表、素材槽位、参数 schema"
    skill: omotion-core
    entry_point: core.invoke.invoke
    dependencies: ["python_deps"]
    priority: 9
    version: ">=0.1.0"
    params:
      template_path: "str - .motion 文件路径"
      decrypt_key: "str, optional - personal 格式解密密钥"
    fallback: null
  - name: motion_template_render
    description: "渲染 .motion 模板为视频，基于 html-canvas/1 运行时 profile，Playwright 抽帧 + FFmpeg 编码，支持参数注入、多分辨率、自定义帧率"
    skill: omotion-core
    entry_point: core.invoke.invoke
    dependencies: ["python_deps", "ffmpeg", "playwright"]
    priority: 10
    version: ">=0.1.0"
    params:
      template_path: "str - .motion 文件路径"
      params: "dict - 模板参数（标题、文本、素材路径等）"
      output_path: "str - 输出 mp4 路径"
      width: "int, optional - 输出宽度，默认 1920"
      height: "int, optional - 输出高度，默认 1080"
      fps: "int, optional - 帧率，默认 60"
    fallback: null
  - name: motion_quality_check
    description: "四层质量门检查：静态校验（模板结构完整性）、参数校验（必填项/类型/范围）、渲染监控（帧丢失/黑屏/超时）、成品检查（分辨率/时长/音画同步）"
    skill: omotion-core
    entry_point: core.invoke.invoke
    dependencies: ["python_deps"]
    priority: 8
    version: ">=0.1.0"
    params:
      template_path: "str - .motion 文件路径"
      params: "dict - 待校验的模板参数"
      output_path: "str, optional - 已渲染视频路径，用于成品检查"
      level: "str, optional - 检查级别：static/params/render/output，默认全部"
    fallback: null
  - name: motion_param_manage
    description: "模板参数管理：JSON Schema 解析、预设管理、参数校验、参数翻译（自然语言→模板参数）"
    skill: omotion-core
    entry_point: core.invoke.invoke
    dependencies: ["python_deps"]
    priority: 7
    version: ">=0.1.0"
    params:
      template_path: "str - .motion 文件路径"
      action: "str - get_schema/validate_params/load_preset/save_preset"
      preset_name: "str, optional - 预设名称"
      params: "dict, optional - 待校验参数"
    fallback: null
  - name: motion_fragment_manage
    description: "动画片段库管理：从模板中提取可复用片段、片段库存储与检索、片段组合到新模板、智能匹配推荐"
    skill: omotion-core
    entry_point: core.invoke.invoke
    dependencies: ["python_deps"]
    priority: 6
    version: ">=0.1.0"
    params:
      action: "str - extract/list/combine/match"
      template_path: "str, optional - 源模板路径（extract 时必填）"
      fragment_id: "str, optional - 片段 ID"
      library_path: "str, optional - 片段库路径"
    fallback: null
---

# O.motion Core — .motion 共享核心库

## 定位

`.motion` 格式专精的共享能力底座，为多个项目提供统一的模板解析、渲染、质量门、参数管理、片段管理能力。

**双重身份**：
1. **Python 库**：直接 `import core.xxx` 使用
2. **可发现 Skill**：ai-video-editor 能力注册中心自动发现，通过 `invoke()` 统一调用

## 能力清单

| 能力名称 | 功能 | 优先级 | 依赖 |
|---------|------|--------|------|
| `motion_template_render` | 模板渲染为视频 | 10 | python, ffmpeg, playwright |
| `motion_template_parse` | 模板解析 | 9 | python |
| `motion_quality_check` | 四层质量门 | 8 | python |
| `motion_param_manage` | 参数管理 | 7 | python |
| `motion_fragment_manage` | 片段库管理 | 6 | python |

## 集成方式

### 方式一：直接 import（O.motion / AI Editor）

```python
import sys
sys.path.insert(0, r"D:\DobaoWork_Project\omotion")

from core.template_parser import parse_template
from core.renderer import RenderPipeline
from core.quality_gate import QualityGate

template = parse_template("template.motion")
pipeline = RenderPipeline(template)
pipeline.render(params={"title": "Hello"}, output="out.mp4")
```

### 方式二：能力注册中心调用（ai-video-editor）

```python
from adapters.capability_registry import CapabilityRegistry

registry = CapabilityRegistry()
result = registry.invoke("motion_template_render", {
    "template_path": "template.motion",
    "params": {"title": "Hello"},
    "output_path": "out.mp4"
})
```

### 方式三：统一 invoke 入口（通用）

```python
from core.invoke import invoke

# 渲染
result = invoke("motion_template_render",
    template_path="template.motion",
    params={"title": "Hello"},
    output_path="out.mp4")

# 质量检查
report = invoke("motion_quality_check",
    template_path="template.motion",
    params={"title": "Hello"})
```

## 格式兼容

严格遵循 [motion-spec](https://github.com/motiontemplates/motion-spec)：
- 内容层：`0.1.0-draft.1`
- 运行时：`html-canvas/1`
- 分发层：`1.0.0-rc.1`（personal-download-v1，可选）

支持两种模板格式：
- **standard**：明文 JSON，阿杜早期个人项目规范
- **personal-download-v1**：AES-256-GCM 加密，MotionGit 平台分发格式

## 项目结构

```
omotion/
├── SKILL.md              # 本文件（能力声明 + 使用手册）
├── core/
│   ├── invoke.py         # 统一调用入口（能力注册中心兼容）
│   ├── template_parser/  # 模板解析器
│   ├── renderer/         # 渲染引擎
│   ├── quality_gate/     # 质量门
│   ├── param_manager/    # 参数管理
│   ├── fragment_manager/ # 片段管理
│   └── utils/            # 工具函数
├── specs/                # 接口规范
├── docs/                 # 文档
├── cli/                  # 命令行工具
└── setup.py
```

## 共享项目

| 项目 | 集成方式 | 用途 |
|------|---------|------|
| O.motion | 直接 import | .motion 专精应用（模板创作+批量产出） |
| AI Editor | SKILL.md 路由 | 总指挥层，.motion 渲染子系统 |
| ai-video-editor | 能力注册中心 | 综合视频编辑，.motion 素材/模板能力 |

## 许可

MIT License
