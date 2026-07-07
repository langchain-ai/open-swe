from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel


class DeferredErrorModel(BaseChatModel):
    """Model placeholder that raises a stored setup error on first invocation."""

    error_message: str
    model_id: str | None = None

    @property
    def _llm_type(self) -> str:
        return "deferred-error"

    def _get_ls_params(self, stop: Any = None, **kwargs: Any) -> dict[str, Any]:
        params = super()._get_ls_params(stop=stop, **kwargs)
        if self.model_id:
            params["ls_model_name"] = self.model_id
            if ":" in self.model_id:
                params["ls_provider"] = self.model_id.split(":", 1)[0]
        return params

    def bind_tools(self, tools: Any, **kwargs: Any) -> DeferredErrorModel:
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
        raise ValueError(self.error_message)


def make_deferred_error_model(
    error: BaseException, *, model_id: str | None = None
) -> BaseChatModel:
    return DeferredErrorModel(error_message=f"{type(error).__name__}: {error}", model_id=model_id)
