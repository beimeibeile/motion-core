# O.motion 接口规范

> 本文档定义 O.motion 核心库的公共接口，所有本地项目（AI Editor、O.motion 等）必须遵循此规范调用，确保跨项目兼容。

## 1. 模板解析接口

### parse_template(path) -> TemplateInfo

自动检测格式并解析 `.motion` 模板。

```python
from core.template_parser import parse_template

template = parse_template("path/to/template.motion")
# template.format: "standard" | "personal-download-v1"
# template.template_id, template.template_version
# template.output_width, template.output_height, template.output_fps
# template.parameters_schema: dict
```

### TemplateInfo 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| path | str | 模板文件路径 |
| format | str | "standard" 或 "personal-download-v1" |
| spec_version | str | 内容层规范版本 |
| template_id | str | 模板唯一标识 |
| template_version | str | 模板版本（SemVer） |
| name | str | 模板名称 |
| runtime_profile | str | 运行时 profile（如 "html-canvas/1"） |
| runtime_entry | str | 运行时入口文件 |
| output_width/height | int | 输出分辨率 |
| output_fps | float | 输出帧率 |
| duration_policy | str | "fixed" / "range" / "fit-voiceover" / "fit-content" |
| parameters_schema | dict | JSON Schema 参数定义 |

## 2. 渲染接口

### RenderPipeline(template_path).render(params, output_path, **kwargs) -> dict

端到端渲染管线。

```python
from core.renderer import RenderPipeline

pipeline = RenderPipeline("template.motion")
result = pipeline.render(
    params={"title": "Hello", "sections": [...]},
    output_path="output.mp4",
    instance_dir="./instances/task-001",  # 可选
    preview=False,  # 可选，低分辨率预览
)
# result: {"success": bool, "output_path": str, "duration": float, "frames": int, "quality_report": QualityReport}
```

## 3. 质量门接口

### QualityGate().check(template, params, output_path=None) -> QualityReport

四层质量检查：静态 → 参数 → 渲染 → 成品。

```python
from core.quality_gate import QualityGate

gate = QualityGate()
report = gate.check(template, params={"title": "Hello"})
# report.status: "pass" | "warn" | "fail"
# report.checks: list[CheckResult]
# report.summary: {"total": N, "passed": N, "warnings": N, "failed": N}
```

### CheckResult 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| stage | str | "static" / "params" / "render" / "output" |
| name | str | 检查项名称 |
| status | str | "pass" / "warn" / "fail" |
| message | str | 详细说明 |

## 4. 参数管理接口

### ParamSchema(schema).validate(params) -> list[str]

校验参数是否符合 JSON Schema。

```python
from core.param_manager import ParamSchema

schema = ParamSchema(template.parameters_schema)
errors = schema.validate({"title": "Hello"})
# errors: 空列表表示通过，否则为错误消息列表
```

### PresetManager

```python
from core.param_manager import PresetManager

pm = PresetManager("./presets")
pm.save("default", {"title": "默认标题"})
params = pm.load("default")
names = pm.list()
```

## 5. 片段管理接口

### FragmentLibrary

```python
from core.fragment_manager import FragmentLibrary

lib = FragmentLibrary("./fragments")
fragments = lib.list(category="text-effect")
results = lib.search("文字强调")
fragment = lib.get("text-highlight-01")
```

### FragmentExtractor.extract(template_path, scene_range, output_dir) -> str

从模板提取片段。

### FragmentCombiner.combine(fragments, output_path) -> str

将多个片段组合为新模板。

## 6. 统一数据格式

### 任务定义（批量产出）

```json
{
  "taskId": "video-001",
  "template": "template-id@version",
  "params": { "...": "..." },
  "assets": {
    "voiceover": "path/to/voice.mp3",
    "images": ["path/to/img1.jpg"]
  },
  "output": "output/video-001.mp4",
  "priority": 5,
  "dependsOn": [],
  "qualityGate": { "level": "standard" }
}
```

### 片段定义

```json
{
  "id": "fragment-id",
  "name": "片段名称",
  "version": "1.0.0",
  "category": "text-effect",
  "tags": ["强调", "文字"],
  "duration": { "min": 2, "max": 5, "default": 3 },
  "runtime": { "profile": "html-canvas/1", "entry": "src/index.html" },
  "parameters": { "schema": "params.schema.json" },
  "dependencies": [],
  "compatibility": ["template-01", "template-02"]
}
```

## 7. 错误码规范

| 错误码 | 含义 |
|--------|------|
| E1001 | 模板文件不存在或无法读取 |
| E1002 | 模板格式不支持 |
| E1003 | 模板结构校验失败 |
| E2001 | 参数校验失败 |
| E2002 | 必需参数缺失 |
| E2003 | 参数超出范围 |
| E3001 | 渲染环境检查失败 |
| E3002 | 渲染进程崩溃 |
| E3003 | 渲染超时 |
| E4001 | 输出文件无法解码 |
| E4002 | 输出时长不匹配 |
| E5001 | 片段不存在 |
| E5002 | 片段组合失败 |
