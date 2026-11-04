# omotion-core API 文档

> 版本：V0.1 | 更新：2026-10-21

`omotion-core` 是 O_Motion 项目的共享核心库，为 AI Editor、ai-video-editor、O_Motion 三个项目提供 .motion 格式的统一处理能力。

---

## 安装

```bash
# 开发模式安装
pip install -e /path/to/motion-core

# 或直接引用
import sys
sys.path.insert(0, "/path/to/motion-core")
```

---

## 模块概览

| 模块 | 导入路径 | 核心类/函数 |
|------|----------|-------------|
| 模板解析 | `core.template_parser` | `detect_format()`, `parse_template()`, `TemplateInfo` |
| 严格校验 | `core.template_parser.strict` | `strict_json_load()`, `lint_template()`, `LintReport` |
| 渲染引擎 | `core.renderer` | `RenderPipeline`, `StandardRenderEngine`, `PersonalRenderEngine`, `RenderError` |
| 质量门 | `core.quality_gate` | `QualityGate`, `QualityReport`, `CheckResult`, `CheckStatus` |
| 抽帧图 | `core.quality_gate.contact_sheet` | `probe_media()`, `generate_contact_sheet()`, `compose_contact_sheet()` |
| 参数管理 | `core.param_manager` | `ParamSchema`, `PresetManager`, `build_standard_params()` |
| 需求处理 | `core.param_manager.requirement_adapter` | `plan_requirement()`, `build_requirement()` |
| 批量生产 | `core.producer` | `TaskManager`, `TemplateIndex`, `BatchQualityReport`, `build_report()`, `deliver()` |
| 环境自检 | `core.doctor` | `run_doctor()`, `DoctorReport`, `DoctorItem`, `check_template()` |
| 统一入口 | `core.invoke` | `invoke(capability, **kwargs)`（17 个能力） |
| CLI | `cli.omotion` | `main()`（render/inspect/validate/probe/audit/doctor/contact-sheet/lint/batch） |

---

## 1. template_parser — 模板解析

### `detect_format(template_path: str) -> str`

自动检测 .motion 模板格式。

**参数：**
- `template_path`：.motion 文件路径

**返回：** 格式字符串，`"standard"` 或 `"personal-download-v1"`

**示例：**
```python
from core.template_parser import detect_format
fmt = detect_format("template.motion")
# "standard" 或 "personal-download-v1"
```

### `parse_template(template_path: str) -> TemplateInfo`

解析模板元数据。

**返回：** `TemplateInfo` 对象，包含以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `path` | str | 模板文件路径 |
| `format` | str | 格式（standard / personal-download-v1） |
| `spec_version` | str | 规范版本 |
| `template_id` | str | 模板 ID |
| `template_version` | str | 模板版本 |
| `name` | str | 模板名称 |
| `runtime_profile` | str | 运行时 profile（如 kinetic-user-core/1, motiongit-host/1） |
| `runtime_entry` | str | 运行时入口（如 adapter.py, motion.mjs） |
| `output_width` | int | 输出宽度 |
| `output_height` | int | 输出高度 |
| `output_fps` | float | 输出帧率 |
| `duration_policy` | str | 时长策略（fit-content / fixed） |
| `duration_seconds` | float / None | 声明时长（秒） |
| `parameters_schema` | dict / None | 参数 JSON Schema |

**示例：**
```python
from core.template_parser import parse_template
info = parse_template("template.motion")
print(f"{info.name} ({info.format})")
print(f"输出: {info.output_width}x{info.output_height} @ {info.output_fps}fps")
```

---

## 2. renderer — 渲染引擎

### `RenderPipeline`

统一渲染入口，自动检测格式并选择对应引擎。

**构造：**
```python
pipeline = RenderPipeline(template_path, instance_dir)
```

**参数：**
- `template_path`：.motion 模板路径
- `instance_dir`：渲染工作目录（解压、中间文件存放处）

**方法：**

#### `render(params: dict, output_path: str, progress_cb=None) -> dict`

执行完整渲染流程。

**参数：**
- `params`：渲染参数字典（格式相关，见下方）
- `output_path`：输出 MP4 路径
- `progress_cb`：可选进度回调 `callback(stage: str, percent: int, message: str)`

**返回：** 渲染结果字典

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | bool | 是否成功 |
| `output_path` | str | 输出文件路径 |
| `elapsed_s` | float | 渲染耗时（秒） |
| `format` | str | 模板格式 |
| `width` | int | 输出宽度 |
| `height` | int | 输出高度 |
| `fps` | float | 输出帧率 |
| `duration_s` | float | 输出时长（秒） |
| `codec` | str | 视频编码 |
| `verified` | bool | ffprobe 验证是否通过 |

**示例：**
```python
from core.renderer import RenderPipeline

def on_progress(stage, pct, msg):
    print(f"[{stage}] {pct}% - {msg}")

pipeline = RenderPipeline("template.motion", "./_work")
result = pipeline.render(params, "output.mp4", progress_cb=on_progress)
print(f"成功: {result['success']}, 耗时: {result['elapsed_s']}s")
```

### `RenderError`

渲染异常，包含阶段信息。

| 属性 | 类型 | 说明 |
|------|------|------|
| `stage` | str | 失败阶段（env/plan/prepare/render/output） |
| `message` | str | 错误信息 |
| `detail` | str | 详细错误（stderr 截断） |

### 参数格式

**standard 格式参数：**
```python
params = {
    "mode": "user-media",
    "language": "zh",          # zh / en
    "layout": "landscape",     # landscape / portrait
    "duration": 8,
    "title": "标题",
    "presenterLabel": "",
    "scenes": [{"id": "B1", "title": "场景1"}],
    "content": {},
    "basic": {"keyword": "关键词", "explanation": "说明文字"},
    "media": {
        "presenter": {"mode": "none"},
        "voice": {"mode": "none"},
        "music": {"mode": "none"}
    },
    "soundEffects": "native",
    "captions": []
}
```

**personal-download-v1 格式参数：**
```python
params = {
    "timeline": {
        "duration": 10,
        "segments": [
            {"control": "title", "t0": 0, "t1": 5, "props": {"line1": "第一段"}},
            {"control": "title", "t0": 5, "t1": 10, "props": {"line1": "第二段"}}
        ],
        "voiceover": "",
        "music": ""
    },
    "workflow": "motion-kit"
}
```

---

## 3. quality_gate — 质量门

### `QualityGate`

四层质量检查：静态 → 参数 → 渲染 → 成品。

**构造：**
```python
gate = QualityGate(config=None)
```

**方法：**

#### `check(template_path, params, output_path=None, render_result=None, expected=None, task_id="") -> QualityReport`

执行所有适用的检查。

**参数：**
- `template_path`：模板路径
- `params`：渲染参数
- `output_path`：输出文件路径（可选，提供则执行成品检查）
- `render_result`：渲染结果字典（可选，提供则执行渲染监控检查）
- `expected`：期望输出属性 `{width, height, duration, require_audio}`
- `task_id`：任务 ID（可选）

**返回：** `QualityReport` 对象

#### 分层检查方法

- `check_static(template_path) -> list[CheckResult]`：文件存在、ZIP有效、格式检测、模板解析
- `check_params(template_path, params) -> list[CheckResult]`：必填字段、类型、数量限制
- `check_render(render_result) -> list[CheckResult]`：渲染成功、输出存在、时长合理性
- `check_output(output_path, expected=None) -> list[CheckResult]`：ffprobe可解码、视频流、分辨率、时长、音频流

### `QualityReport`

| 属性/方法 | 类型 | 说明 |
|-----------|------|------|
| `status` | CheckStatus | 总体状态（PASS/WARN/FAIL） |
| `checks` | list[CheckResult] | 所有检查结果 |
| `summary` | dict | 统计 `{total, passed, warnings, failed}` |
| `to_dict()` | dict | 转为字典 |

### `CheckStatus`

枚举：`PASS`, `WARN`, `FAIL`

### `CheckResult`

| 属性 | 类型 | 说明 |
|------|------|------|
| `stage` | str | 检查阶段（static/params/render/output） |
| `name` | str | 检查项名称 |
| `status` | CheckStatus | 检查结果 |
| `message` | str | 详细信息 |
| `duration_ms` | int | 耗时（毫秒） |

**示例：**
```python
from core.quality_gate import QualityGate, CheckStatus

gate = QualityGate()
report = gate.check(
    "template.motion", params,
    output_path="output.mp4",
    render_result={"success": True, "output_path": "output.mp4", "elapsed_s": 45.0},
    expected={"width": 1920, "height": 1080, "duration": 8.0, "require_audio": False}
)

print(f"状态: {report.status.value}")
print(f"统计: {report.summary}")
for c in report.checks:
    if c.status != CheckStatus.PASS:
        print(f"  [{c.stage}] {c.name}: {c.message}")
```

---

## 4. param_manager — 参数管理

### `ParamSchema`

JSON Schema 解析与校验。

**构造方法：**
- `ParamSchema(schema: dict)`：从字典创建
- `ParamSchema.from_file(path: str)`：从 JSON 文件加载
- `ParamSchema.from_template(template_path: str)`：从 .motion 模板提取

**方法：**

| 方法 | 返回 | 说明 |
|------|------|------|
| `validate(params: dict)` | list[str] | 校验参数，返回错误列表（空列表=通过） |
| `get_defaults()` | dict | 提取默认值 |
| `describe()` | dict | 参数描述（类型、是否必填、说明、默认值、枚举） |
| `list_required()` | list[str] | 必填字段列表 |
| `list_optional()` | list[str] | 可选字段列表 |

**示例：**
```python
from core.param_manager import ParamSchema

schema = ParamSchema.from_template("template.motion")
errors = schema.validate(params)
if errors:
    for e in errors:
        print(f"参数错误: {e}")
else:
    print("参数校验通过")
```

### `PresetManager`

预设管理（保存/加载/列表/删除）。

**构造：**
```python
pm = PresetManager(presets_dir="./presets")
```

**方法：**

| 方法 | 返回 | 说明 |
|------|------|------|
| `list(template_id=None)` | list[str] | 列出预设（可按模板过滤） |
| `load(template_id, name)` | dict / None | 加载预设 |
| `save(template_id, name, params, description="")` | None | 保存预设 |
| `delete(template_id, name)` | bool | 删除预设 |
| `get_info(template_id, name)` | dict / None | 获取预设元数据 |

**示例：**
```python
from core.param_manager import PresetManager

pm = PresetManager("./presets")
pm.save("template-001", "short-video", params, "短视频风格")
loaded = pm.load("template-001", "short-video")
```

---

## 5. doctor — 环境自检（P3）

```python
from core.doctor import run_doctor, check_template

report = run_doctor()                    # 检查 node / ffmpeg / ffprobe / Chrome
report.to_dict()  # {"summary": {"environment": {"ok": 4, "warn": 0, "missing": 0}}, "environment": [...], "healthy": True}
# 注意：summary 是嵌套结构（summary.environment），不是平铺的 ok/warn/missing

report = run_doctor(template_path="t.motion")   # 追加模板结构检查
```

`check_template(path)` 单独检查单个模板（等价 `omotion doctor --template`）。
doctor 只做**存在性**检查，不校验 ffmpeg 版本号是否满足 v2 解析正则。

---

## 6. strict — 严格模板 lint（P3）

```python
from core.template_parser.strict import lint_template, strict_json_load

report = lint_template("t.motion")   # .motion(zip) 或解包目录均可
report.errors, report.warnings       # 元素含 file / line / column 定位
strict_json_load(text, filename=...) # 非法 JSON 抛带 文件:行:列 的异常
```

规则：必需文件 `motion.json / params.schema.json / guide.json`；必需字段
`id / name / version / runtime`（`motion.json` 顶层与 `template` 子对象合并校验）；
可选契约 7 项；`native/` 或 `adapter` 缺失仅 warn。

---

## 7. contact_sheet — 成片抽帧图（P3）

```python
from core.quality_gate.contact_sheet import generate_contact_sheet

png = generate_contact_sheet("out.mp4", "out.contact-sheet.png", frames=12)
```

输出：标题 + 4 列帧网格（带 `t=mm:ss` 时间戳）+ 波形条（#4a7dff）+ 秒刻度。
`probe_media()` 返回时长/分辨率/帧率/音轨信息，供报告复用。

---

## 8. producer — 批量生产

| 类/函数 | 说明 |
|---------|------|
| `TaskManager(tasks_file, template_dir, output_dir)` | 任务 CRUD + `_validate_task` 预校验（模板存在性 / 参数 schema / **渲染管线探测**） |
| `TaskManager.add/update/remove/list_tasks/status_summary` | 任务操作与汇总 |
| `TemplateIndex(template_dir)` | 文件名 → (类型/用途/画幅/版本) + **包内管线探测**（adapter / motion-mjs / unknown） |
| `TemplateIndex.match(topic, duration_s, aspect, voiceover, top, renderable_only=True)` | 智能选型；`pipeline_summary()` 给出产能结构 |
| `BatchQualityReport` / `build_report(manager, with_doctor, with_lint, with_contact_sheet, contact_sheet_frames)` | 批量质量报告（JSON + Markdown，含环境/lint/抽帧三节） |
| `deliver(manager, out_root, archive, day_dir)` | done 成品归档 + manifest |

`_validate_task` 对"开包成功但既无 `adapter.py` 也无 `assistant/motion.mjs`"的任务
直接置 `skipped` 并写原因，不再进入渲染队列；探测失败（无法开包）不做排除。

`TaskManager._load()` 期望 tasks.json **顶层是数组**，非法 JSON 会静默容错成空列表
（报告显示 total=0）——构造任务文件请用 `json.dump`。

---

## 9. CLI — 命令行

```bash
python cli/omotion.py <command> [options]
```

| 命令 | 说明 |
|------|------|
| `render <template> --params <file> --output <mp4>` | 全流程渲染（解析→校验→渲染→质检） |
| `inspect <template>` | 查看模板元数据 |
| `validate <template> --params <file>` | 校验模板和参数 |
| `probe <template>` | 运行时能力表（entry / profile / 是否支持参数化预览） |
| `audit <template>` | copy-contract 参数槽失效审计 |
| `doctor [--template <t>]` | 环境自检（node/ffmpeg/ffprobe/Chrome/模板） |
| `contact-sheet <mp4> [-n 帧数]` | 成片抽帧 contact sheet |
| `lint <template>` | 严格模板结构 lint |
| `batch <tasks.json> [-o report.json] [--markdown] [--no-doctor/--no-lint/--no-contact-sheet]` | 批量质量报告 |

---

## 错误处理

所有渲染异常均为 `RenderError`，包含阶段信息：

```python
from core.renderer import RenderPipeline, RenderError

try:
    result = pipeline.render(params, "output.mp4")
except RenderError as e:
    print(f"渲染失败 [{e.stage}]: {e.message}")
    print(f"详情: {e.detail}")
```

---

## 环境变量

| 变量 | 说明 |
|------|------|
| `CHROME_PATH` | Chrome 可执行文件路径（personal 格式渲染必需） |
| `PYTHONUTF8` | 设为 `1` 可避免 Windows GBK 解码错误（核心库自动设置） |
| `PATH` | 需包含 ffmpeg、ffprobe、node、uv |

---

## 已知限制

- **v2 运行时**：`split_root.py` / `canvas_split_root.py` 在 Windows 上有符号链接 bug；`PersonalRenderEngine` 已支持 v1（motion.mjs + `--workflow motion-kit`）与 v2（auto-route，由 `assistant/BUILD.json` 的 `split-root-v1` profile 判定）自动路由
- **standard 格式**：输出文件不可已存在（adapter.py 拒绝覆盖），核心库自动清理
- **personal 格式**：render 输出路径必须在 instance_dir 内，核心库自动处理后复制到目标路径；解密由包内 `motion.mjs` 运行时完成，不需要额外 API Key
- **可渲染性口径**：`detect_format()`（是否有 `personal/motiongit.key.json`）只判格式，**不等于可渲染性**；可渲染性看包内是否存在 `adapter.py` 或 `assistant/motion.mjs`
- **prepare ≠ render**：`prepare()` 通过只证明结构与运行时入口可用，成片成功率仍需逐模板实渲验证
