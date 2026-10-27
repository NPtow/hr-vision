"""Regression checks for the opt-in interview transition contract.

Also runnable with the production image's stdlib unittest, without pytest or DB:
  DOGRAH_ENGINE_TEST_FILE=/path/to/patched/pipecat_engine.py python this_file.py
"""

import asyncio
import importlib.util
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator


if engine_file := os.getenv("DOGRAH_ENGINE_TEST_FILE"):
    spec = importlib.util.spec_from_file_location("interview_gate_engine", engine_file)
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    PipecatEngine = module.PipecatEngine
else:
    from api.services.workflow.pipecat_engine import PipecatEngine


class InterviewUserResponseGateTests(unittest.IsolatedAsyncioTestCase):
    def make_engine(self, messages=None):
        engine = PipecatEngine.__new__(PipecatEngine)
        self.messages = list(messages or [])
        engine.context = SimpleNamespace(get_messages=lambda: self.messages)
        nodes = {
            name: SimpleNamespace(
                id=name, name=name, is_start=name == "intro", is_end=False,
                allow_interrupt=False,
            )
            for name in ("intro", "question", "b4_exit", "blitz")
        }
        engine._active_agent = SimpleNamespace(
            current_node=None, workflow=SimpleNamespace(nodes=nodes),
            visit_id="visit-test", bind_tool=lambda _, fn, **kwargs: fn,
        )
        engine._require_user_response = True
        engine._response_passthrough_nodes = {"b4_exit"}
        engine._node_entry_user_count = 0
        engine._response_gate_retries = 0
        engine._transition_claim = None
        engine._gathered_context = {}
        engine._node_transition_callback = None
        engine._context_summarization_manager = None
        engine._handle_start_node = AsyncMock()
        engine._handle_agent_node = AsyncMock()
        engine._handle_end_node = AsyncMock()
        engine._perform_variable_extraction_if_needed = AsyncMock()
        engine._run_transition_variable_extraction_in_background = False
        engine._fetch_recording_audio = None
        engine.queue_text_message = AsyncMock()
        return engine

    async def call_transition(self, engine, destination):
        handler = await engine._create_transition_func("next", destination)
        params = SimpleNamespace(arguments={}, result_callback=AsyncMock())
        await handler(params)
        return params

    async def test_opening_cannot_cross_a_conversational_node_without_any_answer(self):
        engine = self.make_engine()
        await engine.set_node("intro")
        for expected_retry in (True, True, False):
            params = await self.call_transition(engine, "question")
            args, kwargs = params.result_callback.await_args
            self.assertEqual(args[0]["status"], "blocked")
            self.assertEqual(kwargs["properties"].run_llm, expected_retry)
            self.assertEqual(engine.active_agent.current_node.id, "intro")
        engine._perform_variable_extraction_if_needed.assert_not_awaited()

    async def test_one_answer_unlocks_only_one_node_then_requires_another_answer(self):
        engine = self.make_engine()
        await engine.set_node("intro")
        self.messages.append({"role": "user", "content": "Мой опыт продаж..."})
        await self.call_transition(engine, "question")
        self.assertEqual(engine.active_agent.current_node.id, "question")
        blocked = await self.call_transition(engine, "blitz")
        self.assertEqual(blocked.result_callback.await_args.args[0]["status"], "blocked")
        self.messages.append({"role": "user", "content": "Конкретный пример..."})
        await self.call_transition(engine, "blitz")
        self.assertEqual(engine.active_agent.current_node.id, "blitz")

    async def test_text_checkpoint_restore_unlocks_after_pending_answer_is_added(self):
        engine = self.make_engine([{"role": "user", "content": "Старый ответ"}])
        await engine.set_node("question", emit_transition_event=False)
        blocked = await self.call_transition(engine, "blitz")
        self.assertEqual(blocked.result_callback.await_args.args[0]["status"], "blocked")
        # text_chat_runner appends this only AFTER restoring the node.
        self.messages.append({"role": "user", "content": "Новый ответ"})
        await self.call_transition(engine, "blitz")
        self.assertEqual(engine.active_agent.current_node.id, "blitz")

    async def test_explicit_passthrough_does_not_need_an_extra_answer(self):
        engine = self.make_engine()
        await engine.set_node("b4_exit")
        await self.call_transition(engine, "blitz")
        self.assertEqual(engine.active_agent.current_node.id, "blitz")

    async def test_synthetic_idle_prompt_does_not_count_as_an_answer(self):
        engine = self.make_engine()
        await engine.set_node("question")
        self.messages.append({
            "role": "user",
            "content": "The user has been quiet. Politely and briefly ask if they're still there in the language that the user has been speaking so far.",
        })
        params = await self.call_transition(engine, "blitz")
        self.assertEqual(params.result_callback.await_args.args[0]["status"], "blocked")

    async def test_parallel_transition_batch_cannot_consume_one_answer_twice(self):
        engine = self.make_engine()
        await engine.set_node("intro")
        self.messages.append({"role": "user", "content": "Один ответ"})
        entered, release = asyncio.Event(), asyncio.Event()

        async def extract(*args, **kwargs):
            entered.set()
            await release.wait()

        engine._perform_variable_extraction_if_needed = extract
        first = asyncio.create_task(self.call_transition(engine, "question"))
        await asyncio.wait_for(entered.wait(), 1)
        second = await self.call_transition(engine, "blitz")
        self.assertEqual(second.result_callback.await_args.args[0]["reason"], "stale_node_transition")
        release.set()
        await first
        self.assertEqual(engine.active_agent.current_node.id, "question")

    async def test_old_handler_is_rejected_after_node_transition(self):
        engine = self.make_engine()
        await engine.set_node("intro")
        stale = await engine._create_transition_func("stale", "blitz")
        self.messages.append({"role": "user", "content": "Ответ"})
        await self.call_transition(engine, "question")
        # Even a later new answer does not authorize a previous node's tool.
        self.messages.append({"role": "user", "content": "Ещё ответ"})
        params = SimpleNamespace(arguments={}, result_callback=AsyncMock())
        await stale(params)
        self.assertEqual(params.result_callback.await_args.args[0]["reason"], "stale_node_transition")
        self.assertEqual(engine.active_agent.current_node.id, "question")

    async def test_cancelled_transition_releases_claim_for_the_next_attempt(self):
        engine = self.make_engine()
        await engine.set_node("intro")
        self.messages.append({"role": "user", "content": "Ответ кандидата"})
        entered = asyncio.Event()

        async def interrupted_extraction(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        engine._perform_variable_extraction_if_needed = interrupted_extraction
        transition = asyncio.create_task(self.call_transition(engine, "question"))
        await asyncio.wait_for(entered.wait(), 1)
        transition.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await transition
        self.assertEqual(engine.active_agent.current_node.id, "intro")
        self.assertIsNone(engine._transition_claim)

        engine._perform_variable_extraction_if_needed = AsyncMock()
        retried = await self.call_transition(engine, "question")
        self.assertEqual(retried.result_callback.await_args.args[0]["status"], "done")
        self.assertEqual(engine.active_agent.current_node.id, "question")

    async def test_stale_sibling_preserves_native_batch_inference_in_both_orders(self):
        # Exercise Pipecat's actual result handler, not just engine state. The
        # accepted result can arrive before its stale sibling; group completion
        # must still generate the question for the newly installed node.
        for order in (("accepted", "stale"), ("stale", "accepted")):
            with self.subTest(order=order):
                engine = self.make_engine()
                await engine.set_node("intro")
                stale_handler = await engine._create_transition_func("stale", "blitz")
                self.messages.append({"role": "user", "content": "Один ответ"})
                accepted = await self.call_transition(engine, "question")
                stale = SimpleNamespace(arguments={}, result_callback=AsyncMock())
                await stale_handler(stale)
                self.assertEqual(engine.active_agent.current_node.id, "question")
                self.assertEqual(
                    stale.result_callback.await_args.args[0]["reason"],
                    "stale_node_transition",
                )

                pending = {
                    name: SimpleNamespace(group_id="transition-batch", tool_call_id=name)
                    for name in order
                }
                callbacks = set()

                async def finish(frame, _in_progress):
                    pending.pop(frame.tool_call_id)

                aggregator = SimpleNamespace(
                    _function_calls_in_progress=pending,
                    _function_calls_image_results={},
                    _user_speaking=False,
                    _handle_function_call_finished=AsyncMock(side_effect=finish),
                    _maybe_push_context_after_function_result=AsyncMock(),
                    _context_updated_tasks=callbacks,
                    _context_updated_task_finished=callbacks.discard,
                    create_task=lambda coro, _name: asyncio.create_task(coro),
                )
                for name in order:
                    result = {"accepted": accepted, "stale": stale}[name]
                    args, kwargs = result.result_callback.await_args
                    await LLMAssistantAggregator._handle_function_call_result(
                        aggregator,
                        SimpleNamespace(
                            function_name=name,
                            tool_call_id=name,
                            result=args[0],
                            properties=kwargs["properties"],
                            run_llm=None,
                        ),
                    )
                self.assertFalse(pending)
                aggregator._maybe_push_context_after_function_result.assert_awaited_once()
                await asyncio.gather(*callbacks)

    async def test_gate_disabled_preserves_original_action_chain_behavior(self):
        engine = self.make_engine()
        engine._require_user_response = False
        await engine.set_node("intro")
        await self.call_transition(engine, "question")
        self.assertEqual(engine.active_agent.current_node.id, "question")


if __name__ == "__main__":
    unittest.main(verbosity=2)
