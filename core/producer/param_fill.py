# -*- coding: utf-8 -*-
"""I2 参数批量填充：数据驱动变量替换 + 字段映射。

能力：
  - substitute(text, vars)：{{key}} / {{key|default}} / {{a.b}} 模板替换
  - fill(value, vars)：递归替换 dict/list/str 中的占位符
  - fill_task(task, vars)：任务参数/名称/输出路径统一替换
  - import_csv / import_json 增强：param 值中 {{列名}} 引用同行/同记录其他字段
"""
import re
from typing import Any, Optional

_TOKEN = re.compile(r"\{\{\s*([^}|]+?)\s*(?:\|\s*([^}]*?)\s*)?\}\}")


def _lookup(path: str, vars: dict, default: Optional[str]) -> Any:
    """按点路径取变量值（vars.a.b），缺省回退 default（未提供则保留原文）。"""
    cur = vars
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def substitute(text: str, vars: dict, keep_missing: bool = True) -> str:
    """替换文本中的 {{path}} 占位符。

    {{a.b}}        取 vars["a"]["b"]
    {{key|默认值}}   缺省默认值
    变量缺失且无默认：keep_missing=True 保留原占位符，否则替换为空串。
    """
    def _rep(m: re.Match) -> str:
        path, default = m.group(1).strip(), m.group(2)
        if default is None:
            value = _lookup(path, vars, _MISSING)
            if value is _MISSING:
                return m.group(0) if keep_missing else ""
            return str(value)
        return str(_lookup(path, vars, default))

    return _TOKEN.sub(_rep, text)


class _Missing:
    pass


_MISSING = _Missing()


def fill(value: Any, vars: dict, keep_missing: bool = True) -> Any:
    """递归填充 dict/list/str 中的 {{var}} 占位符。"""
    if isinstance(value, str):
        return substitute(value, vars, keep_missing)
    if isinstance(value, dict):
        return {k: fill(v, vars, keep_missing) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, vars, keep_missing) for v in value]
    return value


def collect_row_vars(row: dict, param_prefix: str = "param.") -> dict:
    """从数据行收集变量：非参数列的值（供 {{列名}} 引用）。

    嵌套列名 a.b → vars["a"]["b"]；同名冲突后者覆盖。
    专用列 template/name/output 不进入变量。
    """
    reserved = {"template", "name", "output"}
    vars: dict = {}
    for key, value in row.items():
        if key is None or key == "" or key.startswith(param_prefix):
            continue
        if key in reserved:
            continue
        parts = key.split(".")
        node = vars
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    return vars


def fill_params(params: dict, vars: dict) -> dict:
    """批量填充任务参数字典（无占位符的字段原样保留）。"""
    return fill(params, vars)


def fill_task_params(params: dict, vars: dict,
                     keep_missing: bool = True) -> dict:
    """填充单个任务参数；返回新字典，不修改原对象。"""
    return {k: fill(v, vars, keep_missing) for k, v in params.items()}


class ParamFiller:
    """数据驱动参数填充器（I2 统一入口）。"""

    def __init__(self, vars: Optional[dict] = None,
                 keep_missing: bool = True):
        self.vars = dict(vars or {})
        self.keep_missing = keep_missing

    def with_vars(self, extra: dict) -> "ParamFiller":
        return ParamFiller({**self.vars, **extra}, self.keep_missing)

    def fill(self, value: Any) -> Any:
        return fill(value, self.vars, self.keep_missing)

    def fill_task(self, params: dict) -> dict:
        return {k: self.fill(v) for k, v in params.items()}
