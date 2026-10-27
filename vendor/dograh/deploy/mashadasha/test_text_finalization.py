"""Dependency-free regression for the release 1.48 text teardown race.

Load the actual patched helper through AST, avoiding API imports, database
configuration and provider calls. This tests the concurrency contract used by
execute_text_chat_pending_turn, rather than a second copy of that contract.
"""

import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PATCHES = Path(__file__).parent / "patches"
RUNNER = PATCHES / "text_chat_runner.py"
ENGINE = PATCHES / "pipecat_engine.py"


def load_finish_helper():
    tree = ast.parse(RUNNER.read_text())
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_finish_text_chat_pipeline"
    )
    namespace = {
        "asyncio": asyncio,
        "TEXT_CHAT_INTERNAL_CANCEL_REASON": "text_chat_turn_complete",
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(RUNNER), "exec"), namespace)
    return namespace[helper.name]


finish_pipeline = load_finish_helper()


class FakePipeline:
    def __init__(self):
        self.finished = asyncio.Event()
        self.cancel_reasons = []

    def has_finished(self):
        return self.finished.is_set()

    async def cancel(self, *, reason):
        self.cancel_reasons.append(reason)
        self.finished.set()


class TextFinalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_extraction_lands_before_pipeline_cancel_and_snapshot(self):
        pipeline = FakePipeline()
        release_extraction = asyncio.Event()
        extraction_started = asyncio.Event()
        context = {"call_status": "end_call"}

        async def shutdown():
            extraction_started.set()
            await release_extraction.wait()
            self.assertFalse(pipeline.has_finished())
            context["callback_agreed"] = True
            context["extracted_variables"] = {"callback_agreed": True}

        engine = SimpleNamespace(_shutdown_task=asyncio.create_task(shutdown()))
        runner = asyncio.create_task(pipeline.finished.wait())
        finishing = asyncio.create_task(finish_pipeline(
            engine=engine, task=pipeline, runner_task=runner,
        ))
        await extraction_started.wait()
        await asyncio.sleep(0)
        self.assertFalse(finishing.done())
        self.assertFalse(pipeline.has_finished())
        release_extraction.set()
        await asyncio.wait_for(finishing, 1)
        snapshot = dict(context)
        self.assertTrue(snapshot["callback_agreed"])
        self.assertEqual(pipeline.cancel_reasons, ["text_chat_turn_complete"])

    async def test_ordinary_turn_without_shutdown_still_cancels_its_pipeline(self):
        pipeline = FakePipeline()
        runner = asyncio.create_task(pipeline.finished.wait())
        await asyncio.wait_for(finish_pipeline(
            engine=SimpleNamespace(_shutdown_task=None),
            task=pipeline, runner_task=runner,
        ), 1)
        self.assertEqual(pipeline.cancel_reasons, ["text_chat_turn_complete"])

    async def test_shutdown_that_finishes_pipeline_does_not_cancel_it_again(self):
        pipeline = FakePipeline()

        async def shutdown():
            pipeline.finished.set()

        engine = SimpleNamespace(_shutdown_task=asyncio.create_task(shutdown()))
        runner = asyncio.create_task(pipeline.finished.wait())
        await asyncio.wait_for(finish_pipeline(
            engine=engine, task=pipeline, runner_task=runner,
        ), 1)
        self.assertEqual(pipeline.cancel_reasons, [])

    async def test_shutdown_error_cleans_up_pipeline_and_remains_visible(self):
        pipeline = FakePipeline()

        async def shutdown():
            raise RuntimeError("synthetic shutdown failure")

        engine = SimpleNamespace(_shutdown_task=asyncio.create_task(shutdown()))
        runner = asyncio.create_task(pipeline.finished.wait())
        with self.assertRaisesRegex(RuntimeError, "synthetic shutdown failure"):
            await asyncio.wait_for(finish_pipeline(
                engine=engine, task=pipeline, runner_task=runner,
            ), 1)
        self.assertTrue(runner.done())
        self.assertEqual(pipeline.cancel_reasons, ["text_chat_turn_complete"])

    async def test_caller_cancellation_settles_shutdown_before_pipeline_teardown(self):
        pipeline = FakePipeline()
        extraction_started = asyncio.Event()
        extraction_cleaned_up = asyncio.Event()

        async def shutdown():
            extraction_started.set()
            try:
                await asyncio.Future()
            finally:
                self.assertFalse(pipeline.has_finished())
                extraction_cleaned_up.set()

        shutdown_task = asyncio.create_task(shutdown())
        runner = asyncio.create_task(pipeline.finished.wait())
        finishing = asyncio.create_task(finish_pipeline(
            engine=SimpleNamespace(_shutdown_task=shutdown_task),
            task=pipeline, runner_task=runner,
        ))
        await extraction_started.wait()
        await asyncio.sleep(0)
        finishing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await finishing
        self.assertTrue(extraction_cleaned_up.is_set())
        self.assertTrue(shutdown_task.done())
        self.assertTrue(shutdown_task.cancelled())
        self.assertTrue(runner.done())
        self.assertEqual(pipeline.cancel_reasons, ["text_chat_turn_complete"])

    def test_runner_invokes_finalization_helper_before_snapshot(self):
        tree = ast.parse(RUNNER.read_text())
        function = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "execute_text_chat_pending_turn"
        )
        calls = list(ast.walk(function))
        finish = next(node for node in calls if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name)
                      and node.func.id == "_finish_text_chat_pipeline")
        snapshot = next(node for node in calls if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get_gathered_context")
        self.assertLess(finish.lineno, snapshot.lineno)

    def test_final_extraction_timeout_preserves_default_and_accepts_bounded_override(self):
        tree = ast.parse(ENGINE.read_text())
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name)
                    and target.id == "FINAL_EXTRACTION_TIMEOUT_SECONDS"
                    for target in node.targets)
        )
        code = compile(ast.Module(body=[assignment], type_ignores=[]), str(ENGINE), "exec")
        for value, expected in [(None, 10.0), ("60", 60.0), ("0", 1.0), ("999", 120.0)]:
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                if value is not None:
                    os.environ["DOGRAH_FINAL_EXTRACTION_TIMEOUT_SECONDS"] = value
                namespace = {"os": os}
                exec(code, namespace)
                self.assertEqual(namespace["FINAL_EXTRACTION_TIMEOUT_SECONDS"], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
