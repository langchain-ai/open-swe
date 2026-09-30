"""Unit tests for SanitizeToolInputsMiddleware.

Guards against the regression where the LLM generates a string value for an
integer field in read_file (e.g. offset='1, 80'), causing a Pydantic
ValidationError and an unnecessary retry.
"""

from agent.middleware.sanitize_tool_inputs import _sanitize_read_file_args


class TestSanitizeReadFileArgs:
    def test_coerces_offset_with_embedded_json(self) -> None:
        args = {"file_path": "bar.tsx", "offset": '170, "limit": 60', "limit": 60}
        result = _sanitize_read_file_args(args)
        assert result["offset"] == 170

    def test_coerces_offset_with_trailing_comma(self) -> None:
        args = {"file_path": "baz.go", "offset": "1504, ", "limit": 200}
        result = _sanitize_read_file_args(args)
        assert result["offset"] == 1504

    def test_uncoercible_offset_passed_through_unchanged(self) -> None:
        # If no digits at all, we leave the value alone so ToolErrorMiddleware handles it.
        args = {"file_path": "foo.ts", "offset": "bad"}
        result = _sanitize_read_file_args(args)
        assert result["offset"] == "bad"

    def test_does_not_mutate_original_dict(self) -> None:
        args = {"file_path": "foo.ts", "offset": "1, 80"}
        _ = _sanitize_read_file_args(args)
        assert args["offset"] == "1, 80"
