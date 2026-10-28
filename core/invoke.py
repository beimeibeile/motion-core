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
                   output_path: str = None, width: int = 1920,
                   height: int = 1080, fps: int = 60, **kwargs) -> Dict[str, Any]:
    """
    渲染 .motion 模板为视频

    Args:
        template_path: .motion 文件路径
        params: 模板参数
        output_path: 输出 mp4 路径
        width: 输出宽度
        height: 输出高度
        fps: 帧率

    Returns:
        渲染结果 dict（含输出路径、时长、帧数等）
    """
    from core.renderer import RenderPipeline
    pipeline = RenderPipeline(template_path)
    return pipeline.render(
        params=params or {},
        output_path=output_path,
        width=width,
        height=height,
        fps=fps
    )


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
