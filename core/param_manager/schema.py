"""JSON Schema parameter definition parser and validator."""

import json
from pathlib import Path
from typing import Optional

try:
    from jsonschema import Draft202012Validator
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


class ParamSchema:
    """Parse and validate template parameters against params.schema.json.

    Supports both standard (kinetic-user-core) and personal (motiongit-host)
    template parameter schemas.
    """

    def __init__(self, schema: dict):
        self.schema = schema
        self._validator = None
        if HAS_JSONSCHEMA and schema:
            try:
                self._validator = Draft202012Validator(schema)
            except Exception:
                self._validator = None

    @classmethod
    def from_file(cls, path: str) -> "ParamSchema":
        """Load schema from a JSON file path."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data)

    @classmethod
    def from_template(cls, template_path: str) -> Optional["ParamSchema"]:
        """Extract params.schema.json from a .motion template (ZIP)."""
        import zipfile
        try:
            with zipfile.ZipFile(template_path, "r") as z:
                # Try common locations
                for candidate in ["params.schema.json", "template/params.schema.json",
                                  "core/params.schema.json"]:
                    if candidate in z.namelist():
                        data = json.loads(z.read(candidate).decode("utf-8"))
                        return cls(data)
        except Exception:
            pass
        return None

    def validate(self, params: dict) -> list[str]:
        """Validate params against schema. Returns list of error messages.

        Empty list means validation passed.
        """
        errors = []

        if not self.schema:
            return ["schema 为空，无法校验"]

        if not isinstance(params, dict):
            return ["参数必须是字典类型"]

        if self._validator:
            for err in sorted(self._validator.iter_errors(params),
                              key=lambda e: list(e.path)):
                path = ".".join(str(p) for p in err.path) if err.path else "(root)"
                errors.append(f"{path}: {err.message}")
        else:
            # Fallback: basic type checking without jsonschema
            errors = self._basic_validate(params)

        return errors

    def _basic_validate(self, params: dict) -> list[str]:
        """Basic validation when jsonschema is not available.

        覆盖：必填、类型、enum、const、minimum/maximum、maxLength/minLength、
        minItems/maxItems、additionalProperties。
        """
        errors = []
        required = self.schema.get("required", [])
        properties = self.schema.get("properties", {})

        for field in required:
            if field not in params:
                errors.append(f"缺少必填字段: {field}")

        for key, value in params.items():
            if key in properties:
                prop = properties[key]
                expected_type = prop.get("type")

                # 类型检查
                if expected_type == "string" and not isinstance(value, str):
                    errors.append(f"{key}: 期望 string，实际 {type(value).__name__}")
                elif expected_type == "integer" and not isinstance(value, int):
                    errors.append(f"{key}: 期望 integer，实际 {type(value).__name__}")
                elif expected_type == "number" and not isinstance(value, (int, float)):
                    errors.append(f"{key}: 期望 number，实际 {type(value).__name__}")
                elif expected_type == "array" and not isinstance(value, list):
                    errors.append(f"{key}: 期望 array，实际 {type(value).__name__}")
                elif expected_type == "object" and not isinstance(value, dict):
                    errors.append(f"{key}: 期望 object，实际 {type(value).__name__}")

                # const 检查
                if "const" in prop and value != prop["const"]:
                    errors.append(f"{key}: 必须等于固定值 {prop['const']}")

                # enum 检查
                if "enum" in prop and value not in prop["enum"]:
                    errors.append(f"{key}: 不在允许的枚举值中: {prop['enum']}")

                # 数值范围
                if isinstance(value, (int, float)):
                    if "minimum" in prop and value < prop["minimum"]:
                        errors.append(f"{key}: {value} 小于最小值 {prop['minimum']}")
                    if "maximum" in prop and value > prop["maximum"]:
                        errors.append(f"{key}: {value} 大于最大值 {prop['maximum']}")

                # 字符串长度
                if isinstance(value, str):
                    if "maxLength" in prop and len(value) > prop["maxLength"]:
                        errors.append(f"{key}: 长度 {len(value)} 超过上限 {prop['maxLength']}")
                    if "minLength" in prop and len(value) < prop["minLength"]:
                        errors.append(f"{key}: 长度 {len(value)} 小于下限 {prop['minLength']}")

                # 数组长度
                if isinstance(value, list):
                    if "minItems" in prop and len(value) < prop["minItems"]:
                        errors.append(f"{key}: 元素数 {len(value)} 小于下限 {prop['minItems']}")
                    if "maxItems" in prop and len(value) > prop["maxItems"]:
                        errors.append(f"{key}: 元素数 {len(value)} 超过上限 {prop['maxItems']}")

        # additionalProperties 检查（未知字段）
        if self.schema.get("additionalProperties") is False:
            for key in params:
                if key not in properties:
                    errors.append(f"未知字段: {key}（additionalProperties=false）")

        return errors

    def get_defaults(self) -> dict:
        """Extract default values from schema."""
        defaults = {}
        properties = self.schema.get("properties", {})
        for key, prop in properties.items():
            if "default" in prop:
                defaults[key] = prop["default"]
        return defaults

    def describe(self) -> dict:
        """Return human-readable parameter description."""
        properties = self.schema.get("properties", {})
        required = set(self.schema.get("required", []))
        desc = {}
        for key, prop in properties.items():
            desc[key] = {
                "type": prop.get("type", "unknown"),
                "required": key in required,
                "description": prop.get("description", ""),
                "default": prop.get("default"),
                "enum": prop.get("enum"),
            }
        return desc

    def list_required(self) -> list[str]:
        """List required parameter names."""
        return list(self.schema.get("required", []))

    def list_optional(self) -> list[str]:
        """List optional parameter names."""
        required = set(self.schema.get("required", []))
        return [k for k in self.schema.get("properties", {}) if k not in required]
