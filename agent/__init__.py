"""Open SWE Agent package initialization and runtime compatibility monkeypatches."""
# ruff: noqa: E402, F401

import dataclasses
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from langgraph.runtime import _no_op_heartbeat, _no_op_stream_writer
    from langgraph_sdk.runtime import _ExecutionRuntime, _ReadRuntime, _ServerRuntimeBase

    def make_compat_wrapper(original_runtime, overrides=None):
        original_class = original_runtime.__class__

        class DynamicCompatWrapper(original_class):
            __slots__ = ("_original", "_overrides")

            def __init__(self, original, ovrs=None):
                object.__setattr__(self, "_original", original)
                object.__setattr__(self, "_overrides", ovrs or {})

            def __getattr__(self, name: str) -> Any:
                if name in self._overrides:
                    return self._overrides[name]
                return getattr(self._original, name)

            def override(self, **new_overrides: Any) -> Any:
                merged = {**self._overrides, **new_overrides}
                return make_compat_wrapper(self._original, merged)

            def patch_execution_info(self, **overrides: Any) -> Any:
                info = self.execution_info
                if info is None:
                    raise RuntimeError("Cannot patch execution_info before it has been set")
                return self.override(execution_info=info.patch(**overrides))

            @property
            def drain_requested(self) -> bool:
                control = self.control
                return control.drain_requested if control is not None else False

            @property
            def drain_reason(self) -> str | None:
                control = self.control
                return control.drain_reason if control is not None else None

            @property
            def previous(self) -> Any:
                return self._overrides.get("previous", getattr(self._original, "previous", None))

            @property
            def execution_info(self) -> Any:
                return self._overrides.get("execution_info", getattr(self._original, "execution_info", None))

            @property
            def control(self) -> Any:
                return self._overrides.get("control", getattr(self._original, "control", None))

            @property
            def server_info(self) -> Any:
                return self._overrides.get("server_info", getattr(self._original, "server_info", None))

            @property
            def context(self) -> Any:
                return self._overrides.get("context", getattr(self._original, "context", None))

            @property
            def stream_writer(self) -> Any:
                return self._overrides.get("stream_writer", getattr(self._original, "stream_writer", _no_op_stream_writer))

            @property
            def heartbeat(self) -> Any:
                return self._overrides.get("heartbeat", getattr(self._original, "heartbeat", _no_op_heartbeat))

            def merge(self, other: Any) -> Any:
                other_writer = getattr(other, "stream_writer", _no_op_stream_writer)
                self_writer = self.stream_writer
                writer = other_writer if other_writer is not _no_op_stream_writer else self_writer

                other_heartbeat = getattr(other, "heartbeat", _no_op_heartbeat)
                self_heartbeat = self.heartbeat
                heartbeat = other_heartbeat if other_heartbeat is not _no_op_heartbeat else self_heartbeat

                other_prev = getattr(other, "previous", None)
                prev = self.previous if other_prev is None else other_prev

                return make_compat_wrapper(
                    self._original,
                    {
                        "context": getattr(other, "context", None) or self.context,
                        "store": getattr(other, "store", None) or self.store,
                        "stream_writer": writer,
                        "heartbeat": heartbeat,
                        "previous": prev,
                        "execution_info": getattr(other, "execution_info", None) or self.execution_info,
                        "server_info": getattr(other, "server_info", None) or self.server_info,
                        "control": getattr(other, "control", None) or self.control,
                    }
                )

            def __repr__(self):
                return f"CompatWrapper({self._original!r}, overrides={self._overrides!r})"

        DynamicCompatWrapper.__name__ = f"{original_class.__name__}CompatWrapper"
        DynamicCompatWrapper.__qualname__ = f"{original_class.__qualname__}CompatWrapper"

        return DynamicCompatWrapper(original_runtime, overrides)

    # Inject override into _ServerRuntimeBase so all runtime variants have it
    def _runtime_base_override(self, **overrides: Any) -> Any:
        return make_compat_wrapper(self, overrides)

    _ServerRuntimeBase.override = _runtime_base_override

    # Add other attributes/methods to _ServerRuntimeBase as defaults to prevent AttributeError
    # on unwrapped objects.
    _ServerRuntimeBase.previous = property(lambda self: None)
    _ServerRuntimeBase.execution_info = property(lambda self: None)
    _ServerRuntimeBase.control = property(lambda self: None)
    _ServerRuntimeBase.server_info = property(lambda self: None)
    _ServerRuntimeBase.context = property(lambda self: None)
    _ServerRuntimeBase.stream_writer = property(lambda self: _no_op_stream_writer)
    _ServerRuntimeBase.heartbeat = property(lambda self: _no_op_heartbeat)
    _ServerRuntimeBase.drain_requested = property(lambda self: False)
    _ServerRuntimeBase.drain_reason = property(lambda self: None)

    def _runtime_base_patch_execution_info(self, **overrides: Any) -> Any:
        raise RuntimeError("Cannot patch execution_info before it has been set")
    _ServerRuntimeBase.patch_execution_info = _runtime_base_patch_execution_info

    def _runtime_base_merge(self, other: Any) -> Any:
        return make_compat_wrapper(self).merge(other)
    _ServerRuntimeBase.merge = _runtime_base_merge

    logger.info("Successfully applied langgraph_sdk runtime compatibility monkeypatch.")
except Exception as e:
    logger.warning("Failed to apply langgraph_sdk runtime compatibility monkeypatch: %s", e)
