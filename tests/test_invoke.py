# -*- coding: utf-8 -*-
"""Tests for core.invoke (unified capability entry point)."""
import pytest

from core.invoke import invoke, list_capabilities


class TestInvoke:
    def test_unknown_capability(self):
        result = invoke("no_such_capability", x=1)
        assert result["success"] is False
        assert result["code"] == "CAPABILITY_NOT_FOUND"
        assert "no_such_capability" in result["error"]

    def test_list_capabilities(self):
        caps = list_capabilities()
        assert "motion_template_parse" in caps
        assert "motion_template_render" in caps
        assert "motion_quality_check" in caps
        assert "motion_param_manage" in caps
        assert "motion_fragment_manage" in caps

    def test_parse_capability(self, standard_tpl):
        result = invoke("motion_template_parse", template_path=str(standard_tpl))
        assert result["success"] is True
        assert result["result"]["format"] == "standard"

    def test_parse_missing_template(self):
        result = invoke("motion_template_parse", template_path="nope.motion")
        assert result["success"] is False
        assert result["code"] == "EXECUTION_ERROR"

    def test_param_manage_get_schema(self, standard_tpl):
        result = invoke("motion_param_manage",
                        template_path=str(standard_tpl), action="get_schema")
        assert result["success"] is True
        assert "scenes" in result["result"]["properties"]

    def test_param_manage_validate(self, standard_tpl):
        result = invoke("motion_param_manage",
                        template_path=str(standard_tpl), action="validate_params",
                        params={"mode": "standard"})
        assert result["success"] is True
        assert result["result"]["valid"] is False

    def test_param_manage_unknown_action(self, standard_tpl):
        result = invoke("motion_param_manage",
                        template_path=str(standard_tpl), action="nope")
        assert result["success"] is False

    def test_quality_check_capability(self, standard_tpl):
        result = invoke("motion_quality_check",
                        template_path=str(standard_tpl), params={})
        assert result["success"] is True
        assert result["result"]["status"] in ("pass", "warn", "fail")

    def test_quality_check_handles_level(self, standard_tpl):
        """invoke must tolerate the level kwarg without crashing."""
        result = invoke("motion_quality_check",
                        template_path=str(standard_tpl), params={}, level="all")
        assert result["success"] is True
