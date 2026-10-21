# O.motion Core

> `.motion` 格式专精的共享核心库 —— 模板解析、渲染、质量门、片段管理、参数管理

O.motion Core 是一个面向 `.motion` 开放动画模板格式的 Python 共享核心库，为多个本地项目（AI Editor、O.motion 等）提供统一的模板处理能力，避免重复造车。

## 核心能力

| 模块 | 功能 |
|------|------|
| `template_parser` | 解析 standard 和 personal-download-v1 两种 `.motion` 格式 |
| `renderer` | 基于 html-canvas/1 profile 的帧渲染 + FFmpeg 视频编码 |
| `quality_gate` | 静态校验、参数校验、渲染监控、成品检查的四层质量门 |
| `param_manager` | JSON Schema 参数解析、预设管理、参数校验 |
| `fragment_manager` | 动画片段库管理、提取、组合、智能匹配 |
| `utils` | 配置管理、日志、文件操作等工具函数 |

## 格式兼容

严格遵循 [motion-spec](https://github.com/motiontemplates/motion-spec) 规范：
- 内容层：`0.1.0-draft.1`
- 运行时：`html-canvas/1`
- 分发层：`1.0.0-rc.1`（personal-download-v1，可选）

## 安装

```bash
# 开发模式安装（本地项目共享）
pip install -e /path/to/omotion

# 或在项目中引用
import sys
sys.path.insert(0, '/path/to/omotion')
from core.template_parser import parse_template
```

## 快速开始

```python
from core.template_parser import parse_template
from core.renderer import RenderPipeline
from core.quality_gate import QualityGate

# 解析模板
template = parse_template("path/to/template.motion")

# 质量门检查
gate = QualityGate()
report = gate.check(template, params={"title": "Hello"})

# 渲染
pipeline = RenderPipeline(template)
pipeline.render(params={"title": "Hello"}, output="output.mp4")
```

## 项目结构

```
omotion/
├── core/                    # 共享核心库
│   ├── template_parser/     # 模板解析器
│   ├── renderer/            # 渲染器
│   ├── quality_gate/        # 质量门
│   ├── param_manager/       # 参数管理
│   ├── fragment_manager/    # 片段管理
│   └── utils/               # 工具函数
├── specs/                   # 接口规范与数据格式
├── cli/                     # 命令行工具
├── docs/                    # 文档
├── examples/                # 示例
└── tests/                   # 测试
```

## 与其他项目的关系

- **AI Editor**：广义 AI 视频编辑平台，引用本库作为 `.motion` 渲染子系统
- **O.motion**：`.motion` 专精项目，引用本库作为核心，叠加模板创作和批量产出能力
- **MotionGit**：模板分发平台，本库创作的模板可发布到 MotionGit

## 许可

MIT License
