"""
统一调用入口 —— 兼容 ai-video-editor 能力注册中心的 invoke() 接口

ai-video-editor 的 CapabilityRegistry 通过 entry_point 调用本模块的 invoke() 函数，
根据 capability_name 分发到对应核心模块。

使用方式：
    from core.invoke import invoke

    # 渲染模板
    result = invoke("motion_template_render",
        template_path="t.motion",
        params={"title": "Hello"},
        output_path="out.mp4")

    # 质量检查
    report = invoke("motion_quality_check",
        template_path="t.motion",
        params={"title": "Hello"})
"""

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 能力名称 -> 处理函数的映射
# 每个处理函数接收 **kwargs，返回统一格式 dict
_CAPABILITY_HANDLERS: Dict[str, callable] = {}


def _register(name: str):
    """装饰器：注册能力处理函数"""
    def decorator(func):
        _CAPABILITY_HANDLERS[name] = func
        return func
    return decorator


def invoke(capability_name: str, **kwargs) -> Dict[str, Any]:
    """
    统一调用入口

    Args:
        capability_name: 能力名称（如 motion_template_render）
        **kwargs: 能力参数

    Returns:
        统一返回格式：
        {
            "success": bool,
            "capability": str,
            "result": Any,       # 成功时的结果
            "error": str,        # 失败时的错误信息
            "code": str          # 错误码
        }
    """
    handler = _CAPABILITY_HANDLERS.get(capability_name)
    if handler is None:
        available = ", ".join(sorted(_CAPABILITY_HANDLERS.keys()))
        logger.error(f"Unknown capability: {capability_name}. Available: {available}")
        return {
            "success": False,
            "capability": capability_name,
            "result": None,
            "error": f"Unknown capability '{capability_name}'. Available: {available}",
            "code": "CAPABILITY_NOT_FOUND"
        }

    try:
        result = handler(**kwargs)
        return {
            "success": True,
            "capability": capability_name,
            "result": result,
            "error": None,
            "code": None
        }
    except Exception as e:
        logger.exception(f"Capability '{capability_name}' execution failed")
        return {
            "success": False,
            "capability": capability_name,
            "result": None,
            "error": str(e),
            "code": "EXECUTION_ERROR"
        }


def list_capabilities() -> Dict[str, str]:
    """列出所有已注册的能力名称和描述"""
    return {name: func.__doc__ or "No description"
            for name, func in _CAPABILITY_HANDLERS.items()}


# ============ 能力处理函数实现 ============

@_register("motion_template_parse")
def _handle_parse(template_path: str, decrypt_key: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """
    解析 .motion 模板文件

    Args:
        template_path: .motion 文件路径
        decrypt_key: personal 格式解密密钥（兼容参数；加密内容由运行时解密，
                     解析层不提取密钥，故当前忽略）

    Returns:
        模板元数据 dict
    """
    from core.template_parser import parse_template
    info = parse_template(template_path)
    return {
        "path": info.path,
        "format": info.format,
        "spec_version": info.spec_version,
        "id": info.template_id,
        "version": info.template_version,
        "name": info.name,
        "runtime_profile": info.runtime_profile,
        "runtime_entry": info.runtime_entry,
        "output": {"width": info.output_width, "height": info.output_height,
                   "fps": info.output_fps},
        "duration_policy": info.duration_policy,
        "duration_seconds": info.duration_seconds,
        "assets": info.assets,
        "licenses": info.licenses,
        "package_profile": getattr(info, "package_profile", None),
        "runtime_generation": getattr(info, "runtime_generation", None),
        "has_encrypted_content": getattr(info, "has_encrypted_content", None),
    }


@_register("motion_template_render")
def _handle_render(template_path: str, params: Dict[str, Any] = None,
                   output_path: str = None, width: Optional[int] = None,
                   height: Optional[int] = None, fps: Optional[float] = None,
                   **kwargs) -> Dict[str, Any]:
    """
    渲染 .motion 模板为视频

    输出宽高与帧率由模板自身声明决定（写死在模板 runtime 源码里），
    渲染引擎不支持改写。显式传入且与声明不一致时直接报错，
    不做静默忽略——否则调用方会误以为自己改了规格。

    Args:
        template_path: .motion 文件路径
        params: 模板参数
        output_path: 输出 mp4 路径
        width: 可选，输出宽度（仅作校验；须与模板声明一致）
        height: 可选，输出高度（仅作校验；须与模板声明一致）
        fps: 可选，帧率（仅作校验；须与模板声明一致）

    Returns:
        渲染结果 dict（含输出路径、时长、帧数等）
    """
    if not output_path:
        raise ValueError("output_path 必填")

    from core.renderer import RenderPipeline
    pipeline = RenderPipeline(template_path)
    info = pipeline.template_info

    declared = {
        "width": info.output_width,
        "height": info.output_height,
        "fps": info.output_fps,
    }
    mismatched = []
    for key, value in (("width", width), ("height", height), ("fps", fps)):
        if value is None or not declared.get(key):
            continue
        # fps 声明可能为 float/int，按浮点比较避免 60 与 60.0 误报
        delta = (abs(float(value) - float(declared[key])) if key == "fps"
                 else abs(int(value) - int(declared[key])))
        if delta > 0.001:
            mismatched.append(f"{key}={value}（模板声明 {declared[key]}）")

    if mismatched:
        raise ValueError(
            "模板输出规格固定，不支持改写："
            + "；".join(mismatched)
            + "。渲染分辨率/帧率由模板自身声明决定，如需其他规格请选对应画幅的模板。"
        )

    result = pipeline.render(params=params or {}, output_path=output_path)
    # RenderPipeline 失败时返回 success=False 的 dict 而不是抛异常；
    # 这里必须转成异常，否则 invoke 外层仍然报 success=True（假成功）
    if isinstance(result, dict) and result.get("success") is False:
        raise RuntimeError(
            f"渲染失败[{result.get('error_stage', 'unknown')}]: "
            f"{result.get('error') or ''} {result.get('error_detail') or ''}".strip()
        )
    return result


@_register("motion_quality_check")
def _handle_quality_check(template_path: str, params: Dict[str, Any] = None,
                          output_path: Optional[str] = None,
                          level: str = "all", **kwargs) -> Dict[str, Any]:
    """
    四层质量门检查

    Args:
        template_path: .motion 文件路径
        params: 待校验参数
        output_path: 已渲染视频路径（成品检查时需要）
        level: 检查级别 static/params/render/output/all（all 时包含全部可用层）

    Returns:
        质量报告 dict
    """
    from core.quality_gate import QualityGate
    gate = QualityGate()
    report = gate.check(
        template_path=template_path,
        params=params or {},
        output_path=output_path,
        render_result=kwargs.get("render_result"),
        expected=kwargs.get("expected"),
    )
    # level 子集过滤（all 默认不过滤）
    if level != "all":
        allowed = {"static", "params", "render", "output"} & {level}
        report.checks = [c for c in report.checks if c.stage in allowed]
        # 重算整体状态
        if any(c.status.value == "fail" for c in report.checks):
            report.status = "fail"
        elif any(c.status.value == "warn" for c in report.checks):
            report.status = "warn"
        else:
            report.status = "pass"
    return report.to_dict()


@_register("motion_param_manage")
def _handle_param_manage(template_path: str, action: str,
                         preset_name: Optional[str] = None,
                         params: Optional[Dict[str, Any]] = None,
                         **kwargs) -> Dict[str, Any]:
    """
    模板参数管理

    Args:
        template_path: .motion 文件路径
        action: get_schema/validate_params/load_preset/save_preset
        preset_name: 预设名称
        params: 待校验参数

    Returns:
        操作结果 dict
    """
    from core.param_manager.schema import ParamSchema
    from core.param_manager.presets import PresetManager
    from core.template_parser import parse_template

    if action == "get_schema":
        info = parse_template(template_path)
        if not info.parameters_schema:
            raise ValueError("模板无可提取参数 Schema（personal 加密格式）")
        return info.parameters_schema
    elif action == "validate_params":
        info = parse_template(template_path)
        ps = ParamSchema(info.parameters_schema or {})
        errors = ps.validate(params or {})
        return {"valid": not errors, "errors": errors}
    elif action in ("load_preset", "save_preset"):
        info = parse_template(template_path)
        tid = info.template_id
        mgr = PresetManager(kwargs.get("presets_dir", "presets"))
        if action == "load_preset":
            data = mgr.load(tid, preset_name or "")
            if data is None:
                raise ValueError(f"预设不存在: {preset_name}")
            return data
        return mgr.save(tid, preset_name or "default", params or {})
    else:
        raise ValueError(f"Unknown action: {action}. "
                         f"Expected: get_schema/validate_params/load_preset/save_preset")


@_register("motion_fragment_manage")
def _handle_fragment_manage(action: str, template_path: Optional[str] = None,
                            fragment_id: Optional[str] = None,
                            library_path: Optional[str] = None,
                            **kwargs) -> Dict[str, Any]:
    """
    动画片段库管理

    Args:
        action: extract/list/search/get/combine
        template_path: 源模板路径（extract 时必填）
        fragment_id: 片段 ID（get/combine 时必填）
        library_path: 片段库路径

    Returns:
        操作结果 dict
    """
    lib_dir = library_path or "fragment_library"

    if action == "extract":
        from core.fragment_manager.extractor import FragmentExtractor
        if not template_path:
            raise ValueError("template_path is required for extract action")
        out = FragmentExtractor().extract(
            template_path, (0, 0), lib_dir)
        return {"fragment_dir": out}
    elif action == "list":
        from core.fragment_manager.library import FragmentLibrary
        return {"fragments": FragmentLibrary(lib_dir).list(**kwargs)}
    elif action == "search":
        from core.fragment_manager.library import FragmentLibrary
        query = kwargs.get("query", "")
        return {"fragments": FragmentLibrary(lib_dir).search(query)}
    elif action == "get":
        from core.fragment_manager.library import FragmentLibrary
        return {"fragment": FragmentLibrary(lib_dir).get(fragment_id)}
    elif action == "combine":
        from core.fragment_manager.combiner import FragmentCombiner
        fragments = kwargs.get("fragments")
        output = kwargs.get("output_path")
        if not fragments or not output:
            raise ValueError("combine 需要 fragments 列表与 output_path")
        out = FragmentCombiner().combine(fragments, output)
        return {"output_path": out}
    else:
        raise ValueError(f"Unknown action: {action}. "
                         f"Expected: extract/list/search/get/combine")


@_register("producer_task_manage")
def _handle_producer_task(action: str, tasks_file: str = "tasks.json",
                          template_dir: Optional[str] = None,
                          output_dir: Optional[str] = None,
                          template: Optional[str] = None,
                          params: Optional[Dict[str, Any]] = None,
                          output: Optional[str] = None,
                          name: Optional[str] = None,
                          task_id: Optional[str] = None,
                          status: Optional[str] = None,
                          **kwargs) -> Dict[str, Any]:
    """
    批量生产任务管理（Phase 3 I1）

    Args:
        action: import/list/add/update/remove/status/clear/run
        tasks_file: 任务文件 JSON 路径（默认 tasks.json）
        template_dir: 模板库目录（解析相对模板名）
        output_dir: 输出目录（解析相对输出路径）
        template: 模板名（add 时必填）
        params: 模板参数 dict（add 时可选）
        output: 输出路径（add 时可选）
        name: 任务名（add 时可选）
        task_id: 任务 id（update/remove 时必填）
        status: 状态筛选（list 时可选）

    Returns:
        操作结果 dict
    """
    from core.producer import TaskManager, ProducerRunner
    from core.renderer.batch import BatchScheduler

    mgr = TaskManager(tasks_file=tasks_file, template_dir=template_dir,
                      output_dir=output_dir)

    if action == "import":
        file = kwargs.get("file")
        if not file:
            raise ValueError("import 需要 file 参数（CSV 或 JSON 路径）")
        path = Path(file)
        vars_file = kwargs.get("vars_file")
        if path.suffix.lower() == ".csv":
            imported = mgr.import_csv(str(path), vars_file=vars_file)
        else:
            imported = mgr.import_json(str(path), vars_file=vars_file)
        return {"imported": len(imported),
                "summary": mgr.status_summary(),
                "tasks": [t.to_dict() for t in imported]}
    elif action == "list":
        items = mgr.list_tasks(status=status)
        return {"summary": mgr.status_summary(),
                "tasks": [t.to_dict() for t in items]}
    elif action == "add":
        if not template:
            raise ValueError("add 需要 template 参数")
        task = mgr.add(template=template, params=params or {},
                       output=output or "", name=name or "")
        return {"task": task.to_dict(), "summary": mgr.status_summary()}
    elif action == "update":
        if not task_id:
            raise ValueError("update 需要 task_id 参数")
        fields = {k: v for k, v in kwargs.items()
                  if k in ("params", "output", "name", "status", "error")}
        task = mgr.update(task_id, **fields)
        if task is None:
            raise ValueError(f"任务不存在: {task_id}")
        return {"task": task.to_dict()}
    elif action == "remove":
        if not task_id:
            raise ValueError("remove 需要 task_id 参数")
        return {"removed": mgr.remove(task_id),
                "summary": mgr.status_summary()}
    elif action == "status":
        return mgr.status_summary()
    elif action == "clear":
        mgr.clear()
        return {"summary": mgr.status_summary()}
    elif action == "run":
        from core.producer import ProducerRunner
        retry_failed = bool(kwargs.get("retry_failed", False))
        resume = bool(kwargs.get("resume", True))
        jobs = int(kwargs.get("jobs", 1))
        runner = ProducerRunner(
            mgr, resume=resume,
            scheduler_factory=lambda: BatchScheduler(
                jobs=jobs, retry=int(kwargs.get("retry", 1)),
                resume=resume))
        summary = runner.run(retry_failed=retry_failed)
        return {"summary": summary.to_dict(),
                "tasks": [t.to_dict() for t in mgr.list_tasks()]}
    else:
        raise ValueError(f"Unknown action: {action}. "
                         f"Expected: import/list/add/update/remove/status/clear/run")


@_register("producer_template_capabilities")
def _handle_template_capabilities(template_dir: str, layout: Optional[str] = None,
                                  **kwargs) -> Dict[str, Any]:
    """按画幅列出可用模板（参考 adu-motion-video auto-capabilities --layout）。

    Args:
        template_dir: 模板库目录（扫描 *.motion）
        layout: 画幅过滤（横屏 / 竖屏；空 = 全部）

    Returns:
        画幅可用模板清单（不做打分，供选型前先看可用规模）
    """
    from core.producer import TemplateIndex
    index = TemplateIndex(template_dir)
    profiles = index.list_by_aspect(layout)
    return {
        "query": {"layout": layout},
        "total_profiles": len(index.profiles),
        "layout_count": len(profiles),
        "templates": [{"name": p.name, "type": p.type, "purpose": p.purpose,
                       "aspect": p.aspect, "version": p.version,
                       "duration_s": list(p.duration_s),
                       "voiceover": p.voiceover, "recommend": p.recommend}
                      for p in profiles],
    }


@_register("producer_template_select")
def _handle_template_select(template_dir: str, topic: Optional[str] = None,
                            duration_s: Optional[float] = None,
                            aspect: Optional[str] = None,
                            voiceover: Optional[bool] = None,
                            top: int = 3, **kwargs) -> Dict[str, Any]:
    """智能模板选型（Phase 3 I4）：按内容特征自动匹配模板。

    Args:
        template_dir: 模板库目录（扫描 *.motion）
        topic: 主题词（如 "新能源"）
        duration_s: 目标时长（秒）
        aspect: 画幅（横屏/竖屏）
        voiceover: 是否需要口播
        top: 返回前 N 个推荐（默认 3）

    Returns:
        匹配结果：匹配维度 + 按分数降序的模板推荐列表
    """
    from core.producer import TemplateIndex
    index = TemplateIndex(template_dir)
    matches = index.match(topic=topic, duration_s=duration_s,
                          aspect=aspect, voiceover=voiceover, top=top)
    return {
        "query": {"topic": topic, "duration_s": duration_s,
                  "aspect": aspect, "voiceover": voiceover},
        "total_profiles": len(index.profiles),
        "matches": [{"name": p.name, "score": p.score,
                     "type": p.type, "aspect": p.aspect,
                     "voiceover": p.voiceover, "duration_s": list(p.duration_s),
                     "reasons": p.reasons, "recommend": p.recommend}
                    for p in matches],
    }


@_register("producer_batch_report")
def _handle_batch_report(tasks_file: str = "tasks.json",
                         template_dir: Optional[str] = None,
                         output_dir: Optional[str] = None,
                         with_doctor: bool = True,
                         with_lint: bool = True,
                         with_contact_sheet: bool = True,
                         contact_sheet_frames: int = 12,
                         **kwargs) -> Dict[str, Any]:
    """批量质量报告（Phase 3 I5 + OpenFilm P3 并入）：
    聚合渲染结果 + 异常分类 + 重试策略 + doctor 环境自检 +
    逐任务 lint + done 成片 contact sheet。

    Args:
        tasks_file: 任务文件 JSON 路径
        template_dir / output_dir: 与任务管理一致的上下文（透传）
        with_doctor / with_lint / with_contact_sheet: P3 增强开关（默认开）
        contact_sheet_frames: contact sheet 帧数（默认 12）

    Returns:
        完整质量报告（summary/stage_breakdown/error_types/
        retry_strategy/tasks/environment/template_checks/contact_sheets）
    """
    from core.producer import TaskManager, build_report
    mgr = TaskManager(tasks_file=tasks_file, template_dir=template_dir,
                      output_dir=output_dir)
    report = build_report(mgr, with_doctor=with_doctor, with_lint=with_lint,
                          with_contact_sheet=with_contact_sheet,
                          contact_sheet_frames=contact_sheet_frames)
    return {"report": report.to_dict()}


@_register("producer_deliver")
def _handle_producer_deliver(tasks_file: str = "tasks.json",
                             template_dir: Optional[str] = None,
                             output_dir: Optional[str] = None,
                             out_root: Optional[str] = None,
                             archive: bool = True,
                             **kwargs) -> Dict[str, Any]:
    """成品交付与归档（Phase 3 I6）：归档 done 成品 + 生成 manifest。

    Args:
        tasks_file / template_dir / output_dir: 任务上下文
        out_root: 输出根目录（默认 manager.output_dir 或 07-输出成品）
        archive: 复制成品到 成品/{YYYYMMDD}/（默认 True；False 仅 manifest）

    Returns:
        交付结果（archived/missing/summary/manifest 路径）
    """
    from core.producer import TaskManager, deliver
    mgr = TaskManager(tasks_file=tasks_file, template_dir=template_dir,
                      output_dir=output_dir)
    result = deliver(mgr, out_root=out_root, archive=archive)
    return result.to_dict()

@_register("producer_requirement_plan")
def _handle_requirement_plan(catalog_path: str, script: str,
                             layout: str = "landscape",
                             voiceover: bool = True,
                             assets: Optional[Dict[str, Any]] = None,
                             content: Optional[Dict[str, Any]] = None,
                             max_stages: int = 4,
                             **kwargs) -> Dict[str, Any]:
    """模板需求智能处理（P0 / I8 前置）：台词 → 语义阶段 → 场景映射 → 阻塞报告。

    Args:
        catalog_path: 模板 core/catalog.json 绝对路径
        script: 整篇台词/需求文本（必填）
        layout: 画幅 landscape/portrait
        voiceover: 是否口播需求
        assets: 用户素材 {"role_or_name": "/path/..."}（可选；有素材走素材组路线）
        content: 素材组必填文本字段 {"GID.field": "..."}（可选）
        max_stages: 语义阶段上限（默认 4 = B 组上限）

    Returns:
        plan（route/selected/stages/media_required/media_provided/
        blockers/warnings/blocked）+ blockers_report
    """
    from core.producer.requirement_adapter import RequirementAdapter
    ra = RequirementAdapter(catalog_path)
    plan = ra.plan(script=script, layout=layout, voiceover=voiceover,
                   assets=assets, content=content, max_stages=max_stages)
    return {
        "route": plan.route,
        "selected": plan.selected,
        "stages": [{"index": s.index, "title": s.title,
                    "text": s.text, "cues": s.cues} for s in plan.stages],
        "media_required": plan.media_required,
        "media_provided": plan.media_provided,
        "blockers": plan.blockers,
        "warnings": plan.warnings,
        "blocked": plan.blocked,
        "report": plan.blockers_report() if plan.blocked else "",
    }


@_register("producer_requirement_build")
def _handle_requirement_build(catalog_path: str, script: str,
                              layout: str = "landscape",
                              voiceover: bool = True,
                              assets: Optional[Dict[str, Any]] = None,
                              content: Optional[Dict[str, Any]] = None,
                              title: Optional[str] = None,
                              topic: Optional[str] = None,
                              language: str = "zh",
                              presenter_label: str = "",
                              sound_effects: str = "native",
                              captions: Optional[list] = None,
                              max_stages: int = 4,
                              **kwargs) -> Dict[str, Any]:
    """模板需求智能处理：台词 → 契约合法参数（阻塞时返回报告，不出参数）。

    Args:
        同 producer_requirement_plan，另加：
        title/topic/language/presenter_label/sound_effects/captions

    Returns:
        blocked=True 时返回 report/error；否则返回 params + contentEvidence
        + ownershipRefs（覆盖所有保留台词且不重复不错序）
    """
    from core.producer.requirement_adapter import RequirementAdapter
    ra = RequirementAdapter(catalog_path)
    plan = ra.plan(script=script, layout=layout, voiceover=voiceover,
                   assets=assets, content=content, max_stages=max_stages)
    result = ra.build(plan, title=title, topic=topic, language=language,
                      content=content, media=assets,
                      captions=captions, presenter_label=presenter_label,
                      sound_effects=sound_effects)
    if result.blocked:
        return {
            "blocked": True,
            "report": result.error or plan.blockers_report(),
            "params": None,
        }
    return {
        "blocked": False,
        "params": result.params,
        "contentEvidence": result.content_evidence,
        "ownershipRefs": result.ownership_refs,
    }


@_register("motion_runtime_probe")
def _handle_runtime_probe(template_dir: str, **kwargs) -> Dict[str, Any]:
    """模板 runtime 能力表（OpenFilm P0-a 借鉴）：静态探测逐帧直连能力。

    Args:
        template_dir: 模板实例目录（解包后的 core 或实例根目录）

    Returns:
        能力表 dict（kind/clock_risk/render_entry/param_unit/
        supports_t/supports_preview/audio_deps/entry_html/fps）
    """
    from core.runtime.capabilities import probe_runtime
    return probe_runtime(template_dir)


@_register("motion_slot_audit")
def _handle_slot_audit(template_dir: str, **kwargs) -> Dict[str, Any]:
    """参数槽失效审计（OpenFilm P2 借鉴）：copy-contract 维护 + role 契约。

    Args:
        template_dir: 模板实例目录（含 copy-contract.json 与渲染源码）

    Returns:
        审计报告 dict（summary/healthy/findings/warnings；
        只报告不修改，失效槽不自动删）
    """
    from core.audit import audit_slots
    return audit_slots(template_dir).to_dict()


@_register("motion_doctor")
def _handle_doctor(template_dir: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """环境自检（OpenFilm P3 借鉴）：node/ffmpeg/Chrome/模板完整性。

    Args:
        template_dir: 可选模板目录（附加模板完整性检查）

    Returns:
        doctor 报告 dict（healthy/summary/environment/template）
    """
    from core.doctor import run_doctor
    return run_doctor(template_dir).to_dict()


@_register("motion_contact_sheet")
def _handle_contact_sheet(output_path: str, out_path: Optional[str] = None,
                          n: int = 12, **kwargs) -> Dict[str, Any]:
    """contact sheet 生成（OpenFilm P3 借鉴）：12 帧 + 波形 + 秒刻度。

    Args:
        output_path: 成片视频路径
        out_path: 输出 PNG 路径（缺省为 <视频名>.contact-sheet.png）
        n: 帧数（默认 12）

    Returns:
        结果 dict（output/frames/video/duration/has_audio/waveform）
    """
    from core.quality_gate.contact_sheet import generate_contact_sheet
    if not out_path:
        base, _ = os.path.splitext(output_path)
        out_path = f"{base}.contact-sheet.png"
    return generate_contact_sheet(output_path, out_path, n)


@_register("motion_lint")
def _handle_lint(template_dir: str, **kwargs) -> Dict[str, Any]:
    """模板结构 lint（OpenFilm P3 借鉴）：严格解析 + 行号报错。

    Args:
        template_dir: 模板目录（或 .motion 解包目录）

    Returns:
        lint 报告 dict（healthy/summary/findings）
    """
    from core.template_parser.strict import lint_template
    return lint_template(template_dir).to_dict()
