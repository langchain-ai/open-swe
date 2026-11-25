"""Unit tests for deferring unsupported Terminal-Bench environment checks into start()."""

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from harbor.environments.langsmith import LangSmithEnvironment
from harbor.models.task.config import EnvironmentConfig, TaskOS, TpuSpec
from harbor.models.trial.paths import TrialPaths

from harbor_adapter import RobinSandbox


class DeferredValidationTest(unittest.TestCase):
    def _make_sandbox(
        self,
        *,
        task_env_config: EnvironmentConfig,
        compose: bool = False,
    ) -> RobinSandbox:
        tmp = Path(tempfile.mkdtemp(prefix="robin-tb-test-"))
        self.addCleanup(lambda: _rmtree(tmp))
        env_dir = tmp / "environment"
        env_dir.mkdir()
        if compose:
            (env_dir / "docker-compose.yaml").write_text("services:\n  main:\n    image: alpine\n")
        else:
            (env_dir / "Dockerfile").write_text("FROM alpine:latest\n")
        trial_dir = tmp / "trial"
        trial_dir.mkdir()
        return RobinSandbox(
            environment_dir=env_dir,
            environment_name="test-task",
            session_id="test-session",
            trial_paths=TrialPaths(trial_dir=trial_dir),
            task_env_config=task_env_config,
            api_key="test-key",
        )

    def test_gpu_task_defers_error_to_start(self) -> None:
        sandbox = self._make_sandbox(task_env_config=EnvironmentConfig(gpus=1))
        with (
            patch.object(LangSmithEnvironment, "start", new=AsyncMock()) as super_start,
            self.assertRaisesRegex(RuntimeError, "GPU"),
        ):
            asyncio.run(sandbox.start(force_build=False))
        super_start.assert_not_awaited()

    def test_tpu_task_defers_error_to_start(self) -> None:
        sandbox = self._make_sandbox(
            task_env_config=EnvironmentConfig(tpu=TpuSpec(type="v5e", topology="2x2"))
        )
        with (
            patch.object(LangSmithEnvironment, "start", new=AsyncMock()) as super_start,
            self.assertRaisesRegex(RuntimeError, "TPU"),
        ):
            asyncio.run(sandbox.start(force_build=False))
        super_start.assert_not_awaited()

    def test_windows_task_defers_error_to_start(self) -> None:
        sandbox = self._make_sandbox(task_env_config=EnvironmentConfig(os=TaskOS.WINDOWS))
        with (
            patch.object(LangSmithEnvironment, "start", new=AsyncMock()) as super_start,
            self.assertRaisesRegex(RuntimeError, "Windows"),
        ):
            asyncio.run(sandbox.start(force_build=False))
        super_start.assert_not_awaited()

    def test_compose_task_defers_error_to_start(self) -> None:
        sandbox = self._make_sandbox(task_env_config=EnvironmentConfig(), compose=True)
        with (
            patch.object(LangSmithEnvironment, "start", new=AsyncMock()) as super_start,
            self.assertRaisesRegex(RuntimeError, "docker-compose"),
        ):
            asyncio.run(sandbox.start(force_build=False))
        super_start.assert_not_awaited()

    def test_supported_task_calls_super_start(self) -> None:
        sandbox = self._make_sandbox(task_env_config=EnvironmentConfig())
        with patch.object(LangSmithEnvironment, "start", new=AsyncMock()) as super_start:
            asyncio.run(sandbox.start(force_build=True))
        super_start.assert_awaited_once_with(force_build=True)


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
