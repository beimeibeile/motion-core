"""Auto parameter generation from params.schema.json (JSON Schema -> valid defaults).

策略（按优先级）:
  1. 用户提供值（不覆盖）
  2. const（固定值）
  3. default（schema 默认值）
  4. enum（第一项）
  5. 类型兜底: string->"" / number|integer->minimum 或 1 / boolean->false /
     array->minItems 递归生成 / object->{}

用于: 批量渲染默认参数、片段验证参数、模板冒烟测试。
"""
from typing import Any


def build_default_params(schema: dict, user_params: dict | None = None,
                         fill_optional: bool = True) -> dict:
    """从 JSON Schema 生成合法默认参数（不覆盖用户已提供的字段）。

    Args:
        schema: JSON Schema（含 properties/required）
        user_params: 用户已提供参数（优先保留）
        fill_optional: 是否填充可选字段（默认 True）。
            False 时仅生成 schema required 字段——老版 kinetic 模板的
            可选字段（如 markWindows）有内部语义，自动占位值会触发
            adapter 校验失败，应留给模板自身默认。
    """
    params = dict(user_params or {})
    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    for key, prop in props.items():
        if key in params:
            continue
        if not fill_optional and key not in required:
            continue
        params[key] = _gen_value(prop, required=key in required)
    return params


def _gen_value(prop: dict, required: bool = False) -> Any:
    """为单个属性生成合法值。"""
    if "const" in prop:
        return prop["const"]
    if "default" in prop:
        return prop["default"]
    if "enum" in prop:
        return prop["enum"][0]

    t = prop.get("type")
    # oneOf/anyOf: 取第一分支
    if t is None:
        for branch_key in ("oneOf", "anyOf", "allOf"):
            branches = prop.get(branch_key)
            if isinstance(branches, list) and branches:
                return _gen_value(branches[0], required=required)
    if t is None:
        return {}

    if t == "string":
        if required:
            return "示例内容"
        return ""
    if t in ("number", "integer"):
        minimum = prop.get("minimum", 1)
        if isinstance(minimum, (int, float)):
            return float(minimum) if t == "number" else int(minimum)
        return 1
    if t == "boolean":
        return False
    if t == "array":
        items = prop.get("items", {})
        min_items = prop.get("minItems", 1)
        return [_gen_value(items, required=True) for _ in range(min_items)]
    if t == "object":
        obj_schema = dict(prop)
        obj_props = obj_schema.get("properties", {})
        return build_default_params({"properties": obj_props,
                                     "required": obj_schema.get("required", [])})
    if t == "null":
        return None
    return {}


def build_default_scenes(scene_schema: dict, count: int = 1) -> list[dict]:
    """从 scenes items schema 生成 N 个合法场景。"""
    items = scene_schema.get("items", {})
    item_props = items.get("properties", {})
    scenes = []
    for i in range(count):
        scene = build_default_params({"properties": item_props})
        # 确保场景可识别（id/title 为空时填充）
        if "id" in item_props and not scene.get("id"):
            scene["id"] = f"S{i + 1:02d}"
        if "title" in item_props and not scene.get("title"):
            scene["title"] = f"场景 {i + 1}"
        scenes.append(scene)
    return scenes
