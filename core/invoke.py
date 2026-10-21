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
        decrypt_key: personal 格式解密密钥（可选）

    Returns:
        模板元数据 dict
    """
    from core.template_parser import parse_template
    return parse_template(template_path, decrypt_key=decrypt_key)


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
        level: 检查级别 static/params/render/output/all

    Returns:
        质量报告 dict
    """
    from core.quality_gate import QualityGate
    gate = QualityGate()
    return gate.check(
        template_path=template_path,
        params=params or {},
        output_path=output_path,
        level=level
    )


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
    from core.param_manager import ParamManager
    mgr = ParamManager(template_path)

    if action == "get_schema":
        return mgr.get_schema()
    elif action == "validate_params":
        return mgr.validate(params or {})
    elif action == "load_preset":
        return mgr.load_preset(preset_name)
    elif action == "save_preset":
        return mgr.save_preset(preset_name, params or {})
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
        action: extract/list/combine/match
        template_path: 源模板路径（extract 时必填）
        fragment_id: 片段 ID
        library_path: 片段库路径

    Returns:
        操作结果 dict
    """
    from core.fragment_manager import FragmentLibrary
    lib = FragmentLibrary(library_path)

    if action == "extract":
        if not template_path:
            raise ValueError("template_path is required for extract action")
        return lib.extract_from_template(template_path)
    elif action == "list":
        return lib.list_fragments()
    elif action == "combine":
        return lib.combine(fragment_id, **kwargs)
    elif action == "match":
        return lib.match(**kwargs)
    else:
        raise ValueError(f"Unknown action: {action}. "
                         f"Expected: extract/list/combine/match")
