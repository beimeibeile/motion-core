# O.motion 架构文档

## 总体架构

```
┌──────────────────────────────────────────────────────┐
│                   应用层 (Applications)                │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │
│  │  AI Editor   │  │   O.motion   │  │  未来项目...  │  │
│  │  (广义视频)   │  │  (.motion专精)│  │              │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘  │
├─────────┼────────────────┼─────────────────┼──────────┤
│         │         共享核心层 (omotion-core)            │
│  ┌──────▼────────────────▼─────────────────▼───────┐  │
│  │              统一接口层 (API Layer)               │  │
│  │  parse_template | RenderPipeline | QualityGate   │  │
│  │  ParamSchema | FragmentLibrary | PresetManager   │  │
│  ├─────────────────────────────────────────────────┤  │
│  │              核心服务层 (Core Services)            │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │  │
│  │  │ 模板解析  │ │  渲染器   │ │    质量门        │ │  │
│  │  │ Parser   │ │ Renderer │ │  Quality Gate    │ │  │
│  │  └──────────┘ └──────────┘ └──────────────────┘ │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │  │
│  │  │ 参数管理  │ │ 片段管理  │ │    工具函数       │ │  │
│  │  │  Params  │ │ Fragment │ │    Utils         │ │  │
│  │  └──────────┘ └──────────┘ └──────────────────┘ │  │
│  └─────────────────────────────────────────────────┘  │
├───────────────────────────────────────────────────────┤
│                  基础设施层 (Infrastructure)            │
│   Python 3.10+ | Node.js | FFmpeg | Playwright/Chrome  │
└───────────────────────────────────────────────────────┘
```

## 设计原则

1. **单一职责**：每个模块只负责一件事，接口清晰
2. **依赖倒置**：应用层依赖核心库接口，不依赖具体实现
3. **可替换**：渲染引擎、质量门检查项可配置替换
4. **可扩展**：新模板格式、新运行时 profile 通过扩展实现
5. **质量门前置**：所有渲染必须通过质量门，不允许跳过

## 模块依赖关系

```
template_parser ← quality_gate (静态校验)
param_manager  ← quality_gate (参数校验)
renderer       ← quality_gate (渲染监控+成品检查)
fragment_manager → template_parser (提取片段)
               → renderer (组合后渲染)
utils → 所有模块
```

## 跨项目共享方式

### 方式一：pip install -e（推荐）

```bash
# 在 omotion 核心库目录
pip install -e .

# 在任意项目中
from core.template_parser import parse_template
```

### 方式二：sys.path 插入

```python
import sys
sys.path.insert(0, r"D:\DobaoWork_Project\omotion")
from core.renderer import RenderPipeline
```

### 方式三：Git submodule

将 omotion 作为子模块嵌入各项目。

## 版本兼容策略

- 核心库版本：SemVer（0.1.0 → 0.2.0 → ... → 1.0.0）
- 接口变更：主版本号升级时才允许 breaking change
- 次版本号：新增功能，保持向后兼容
- 修订号：bug 修复

## 与 motion-spec 的关系

O.motion 核心库严格遵循 [motion-spec](https://github.com/motiontemplates/motion-spec)：
- 内容层：`0.1.0-draft.1`
- 运行时：`html-canvas/1`
- 分发层：`1.0.0-rc.1`（可选）

不发明私有格式，确保与 MotionGit 平台完全兼容。
