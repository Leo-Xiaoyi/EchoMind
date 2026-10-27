import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from anthropic import AsyncAnthropic

from core.llm_utils import create_message, extract_text_content


def model_response(content, stop_reason="end_turn"):
    return httpx.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant",
        "model": "deepseek-v4-pro", "content": content,
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 20, "output_tokens": 30},
    })


@pytest.mark.parametrize("task", [
    "intent", "rewrite", "rerank", "agent", "composer", "judge",
    "profile", "compress", "merge_summary",
])
def test_deepseek_bounded_tasks_do_not_spend_answer_budget_on_thinking(task):
    """Emulate the live failure at the HTTP boundary, including tool follow-ups."""
    async def run():
        requests = []
        text = {
            "intent": '{"intent":"greeting","confidence":0.95}',
            "rewrite": '["退款到账时间"]',
            "rerank": '[2,0,1]',
            "judge": '{"relevance":0.9,"accuracy":0.9,"completeness":0.9,"helpfulness":0.9}',
            "profile": '{"preferences":[],"entities":{}}',
        }.get(task, "处理完成")

        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            if body.get("thinking") != {"type": "disabled"}:
                return model_response([], "max_tokens")
            if task == "agent" and len(requests) == 1:
                return model_response([{"type": "tool_use", "id": "tool_1",
                                        "name": "lookup_error_code", "input": {"error_code": "401"}}], "tool_use")
            return model_response([{"type": "text", "text": text}])

        async with AsyncAnthropic(api_key="test-only", base_url="https://api.deepseek.com/anthropic",
                                  http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))) as client:
            if task == "intent":
                from core.intent_recognizer import IntentRecognizer, IntentCategory
                rec = IntentRecognizer.__new__(IntentRecognizer)
                rec.client, rec.model = client, "deepseek-v4-pro"
                result = await rec._llm_recognize("你好", None)
                assert result["intent"] is IntentCategory.GREETING and not result.get("failed")
            elif task in ("rewrite", "rerank"):
                from tooling.tool_manager import MCPToolManager
                manager = MCPToolManager.__new__(MCPToolManager)
                manager._client, manager._model = client, "deepseek-v4-pro"
                if task == "rewrite":
                    assert await manager.rewrite_query("退款") == ["退款", "退款到账时间"]
                else:
                    assert await manager._rerank_with_status("退款", ["a", "b", "c"], 2) == (["c", "a"], True)
            elif task in ("agent", "composer"):
                from agents.agent_orchestrator import TechnicalAgent, ResponseComposer, Request, AgentResponse, AgentType
                req = Request(message="登录报401，而且重复扣款", user_id="u", conv_id="c")
                if task == "agent":
                    result = await TechnicalAgent(client, "deepseek-v4-pro").handle(req)
                    assert result.success and result.status == "success"
                    assert result.tools_used == ["lookup_error_code"]
                    assert len(requests) == 2
                else:
                    result = await ResponseComposer(client, "deepseek-v4-pro").compose_result(req, [
                        AgentResponse(AgentType.TECHNICAL, "技术诊断", True),
                        AgentResponse(AgentType.BILLING, "账单核验", True),
                    ])
                    assert result.content == text and result.status == "success"
            elif task == "judge":
                from evaluation.evaluator import LLMJudge
                result = await LLMJudge(client, "deepseek-v4-pro").judge("问题", "回答")
                assert not result.judge_failed and result.attempts == 1
            else:
                from memory.conversation_memory import MemoryManager
                memory = MemoryManager.__new__(MemoryManager)
                memory._client, memory._model = client, "deepseek-v4-pro"
                if task == "profile":
                    memory._get_working_memory = AsyncMock(return_value=[SimpleNamespace(role=SimpleNamespace(value="user"), content="退款")])
                    memory._get_profile = AsyncMock(return_value={})
                    memory._profile = SimpleNamespace(upsert=Mock())
                    await memory._update_profile("u", "c")
                    memory._profile.upsert.assert_called_once()
                    assert json.loads(memory._profile.upsert.call_args.kwargs["documents"][0]) == json.loads(text)
                elif task == "merge_summary":
                    assert await memory._merge_summary("旧摘要", "新摘要") == text
                else:
                    import fakeredis.aioredis
                    snapshot = [json.dumps({"role":"user", "content":"退款", "ts":"2026-10-25T12:00:00"})] * memory.COMPRESS_AT
                    memory._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
                    await memory._redis.lpush(memory._wm_key("u", "c"), *snapshot)
                    memory._store_episodic = AsyncMock()
                    await memory._compress("u", "c")
                    memory._store_episodic.assert_awaited_once()
                    assert memory._store_episodic.call_args.args[-1] == text
                    assert await memory._redis.llen(memory._wm_key("u", "c")) == 5
                    assert await memory._redis.get(memory._summary_key("u", "c")) == text
                    await memory._redis.aclose()
            assert requests and all(body["thinking"] == {"type": "disabled"} for body in requests)
    asyncio.run(run())


@pytest.mark.parametrize("base_url", [
    "https://api.anthropic.com", "https://other-provider.example/anthropic",
    "https://api.deepseek.com.example/anthropic",
])
def test_other_providers_keep_original_request_body(base_url):
    async def run():
        def handle(request):
            body = json.loads(request.content)
            assert "thinking" not in body and body["max_tokens"] == 256
            return model_response([{"type":"text", "text":"ok"}])
        async with AsyncAnthropic(api_key="test-only", base_url=base_url,
                                  http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))) as client:
            result = await create_message(client, model="test", max_tokens=256, messages=[{"role":"user","content":"hello"}])
            assert extract_text_content(result.content) == "ok"
    asyncio.run(run())


def test_explicit_thinking_and_extra_body_are_preserved():
    async def run():
        create = AsyncMock()
        client = SimpleNamespace(base_url="https://api.deepseek.com/anthropic", messages=SimpleNamespace(create=create))
        explicit = {"thinking":{"type":"enabled"}, "metadata":{"user_id":"u"}}
        await create_message(client, extra_body=explicit)
        assert create.call_args.kwargs["extra_body"] == explicit
        body = {"metadata":{"user_id":"u"}}
        await create_message(client, extra_body=body)
        assert create.call_args.kwargs["extra_body"] == {**body, "thinking":{"type":"disabled"}}
        assert body == {"metadata":{"user_id":"u"}}
    asyncio.run(run())
