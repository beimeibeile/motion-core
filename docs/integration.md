# 项目集成指南

## AI Editor 项目集成

AI Editor 是广义 AI 视频编辑平台，需要 `.motion` 渲染能力作为子系统。

### 集成步骤

1. **安装核心库**
```bash
cd D:\DobaoWork_Project\omotion
pip install -e .
```

2. **替换原有 adu-driver 调用**
```python
# 旧方式（直接调用 adu-driver 脚本）
# subprocess.run(["python", "drivers/adu-driver/motiongit_runner.py", ...])

# 新方式（调用 omotion-core 接口）
from core.renderer import RenderPipeline
from core.quality_gate import QualityGate

pipeline = RenderPipeline(template_path)
result = pipeline.render(params=params, output_path=output)
```

3. **保留 adu-driver 作为参考实现**
   - adu-driver 的成熟渲染逻辑逐步迁移到 omotion-core
   - 迁移期间两者可共存
   - 完成后 adu-driver 标记为 deprecated

## O.motion 项目集成

O.motion 是 `.motion` 专精项目，核心库是其基础。

### 集成方式

O.motion 项目目录（`D:\DobaoWork_Project\O.motion`）通过以下方式引用核心库：

```python
# O.motion 项目中的代码
import sys
sys.path.insert(0, r"D:\DobaoWork_Project\omotion")

from core.template_parser import parse_template
from core.renderer import RenderPipeline
from core.quality_gate import QualityGate
from core.fragment_manager import FragmentLibrary
```

### O.motion 在核心库之上的扩展

O.motion 项目在核心库基础上叠加：
- 模板创作工具链（脚手架、实时预览、打包器）
- 批量产出调度系统（任务队列、智能选型、并行渲染）
- MotionGit 集成（发布工具、API 客户端）
- 项目特定的模板库和片段库

## 共享成果清单

以下成果在核心库中统一维护，各项目共享：

| 成果 | 位置 | 说明 |
|------|------|------|
| 模板解析器 | core/template_parser/ | 支持两种格式 |
| 渲染引擎 | core/renderer/ | Playwright + FFmpeg |
| 质量门 | core/quality_gate/ | 四层检查 |
| 参数管理 | core/param_manager/ | Schema + 预设 |
| 片段管理 | core/fragment_manager/ | 库 + 提取 + 组合 |
| 接口规范 | specs/ | 所有项目必须遵循 |
| 错误码 | specs/api-interfaces.md | 统一错误定义 |

## 避免重复造车规则

1. **先查核心库**：开发新功能前，先检查核心库是否已有实现
2. **通用能力下沉**：如果某个功能在两个以上项目中需要，应下沉到核心库
3. **接口统一**：所有项目调用 `.motion` 相关功能必须通过核心库接口，不直接操作底层
4. **Bug 修复共享**：在核心库中修复的 bug，所有项目自动受益
5. **定期同步**：核心库更新后，各项目应及时升级版本
