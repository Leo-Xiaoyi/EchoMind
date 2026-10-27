import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from core.llm_utils import create_message
from core.performance import collect_performance, performance_snapshot, measure_stage
from memory.conversation_memory import MemoryManager, Message, MsgRole
from tooling.tool_manager import MCPToolManager, Tool


def search_manager(ttl=30):
    manager = MCPToolManager("test")
    async def retrieve(params, context):
        return [{"content": "退款规则", "document_id": "refund"}]
    manager.register(Tool("search", "", retrieve, {"type": "object"}, cache_ttl=ttl))
    manager.rewrite_query = AsyncMock(return_value=["退款"])
    return manager


@pytest.mark.asyncio
async def test_full_search_cache_skips_models_and_returns_defensive_copy():
    manager = search_manager()
    try:
        first = await manager.search_with_rewrite("search", "退款")
        first.data[0]["content"] = "changed"
        with collect_performance():
            second = await manager.search_with_rewrite("search", "退款")
            assert performance_snapshot()["events"]["rag_pipeline_cache_hit"] == 1
        assert second.cached and second.data[0]["content"] == "退款规则"
        assert manager.rewrite_query.await_count == 1
    finally:
        await manager._client.close()


@pytest.mark.asyncio
async def test_concurrent_identical_searches_share_pipeline():
    manager = search_manager()
    started, release = asyncio.Event(), asyncio.Event()
    async def rewrite(*args, **kwargs):
        started.set()
        await release.wait()
        return ["退款"]
    manager.rewrite_query = AsyncMock(side_effect=rewrite)
    try:
        first = asyncio.create_task(manager.search_with_rewrite("search", "退款"))
        await started.wait()
        rest = [asyncio.create_task(manager.search_with_rewrite("search", "退款")) for _ in range(4)]
        release.set()
        results = await asyncio.gather(first, *rest)
        assert manager.rewrite_query.await_count == 1
        assert sum(row.cached for row in results) == 4
        assert not manager._search_locks._locks
    finally:
        await manager._client.close()


@pytest.mark.asyncio
async def test_pipeline_cache_respects_user_top_k_and_invalidation():
    manager = search_manager()
    try:
        await manager.search_with_rewrite("search", "退款", context={"user_id": "a"})
        await manager.search_with_rewrite("search", "退款", context={"user_id": "b"})
        await manager.search_with_rewrite("search", "退款", top_k=2, context={"user_id": "a"})
        assert manager.rewrite_query.await_count == 3
        manager.invalidate_cache("search")
        assert not (await manager.search_with_rewrite("search", "退款", context={"user_id": "a"})).cached
        assert manager.rewrite_query.await_count == 4
        clone = manager.fork()
        clone.rewrite_query = AsyncMock(return_value=["退款"])
        assert not (await clone.search_with_rewrite("search", "退款", context={"user_id": "a"})).cached
    finally:
        await manager._client.close()


@pytest.mark.asyncio
async def test_invalidated_inflight_search_cannot_restore_old_pipeline_result():
    manager = search_manager()
    started, release = asyncio.Event(), asyncio.Event()
    async def rewrite(*args, **kwargs):
        started.set()
        await release.wait()
        return ["退款"]
    manager.rewrite_query = AsyncMock(side_effect=rewrite)
    try:
        pending = asyncio.create_task(manager.search_with_rewrite("search", "退款"))
        await started.wait()
        manager.invalidate_cache("search")
        release.set()
        await pending
        assert not (await manager.search_with_rewrite("search", "退款")).cached
        assert manager.rewrite_query.await_count == 2
    finally:
        await manager._client.close()


@pytest.mark.asyncio
async def test_zero_ttl_and_failed_searches_are_not_pipeline_cached():
    manager = search_manager(ttl=0)
    try:
        await manager.search_with_rewrite("search", "退款")
        assert not (await manager.search_with_rewrite("search", "退款")).cached
        assert manager.rewrite_query.await_count == 2
        manager.register(Tool("search", "", AsyncMock(side_effect=RuntimeError("down")), {}, cache_ttl=30))
        assert not (await manager.search_with_rewrite("search", "退款")).success
        assert not (await manager.search_with_rewrite("search", "退款")).success
        assert manager.rewrite_query.await_count == 4
    finally:
        await manager._client.close()


@pytest.mark.asyncio
async def test_cancelled_search_releases_single_flight_lock():
    manager = search_manager()
    started = asyncio.Event()
    async def stall(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    manager.rewrite_query = AsyncMock(side_effect=stall)
    task = asyncio.create_task(manager.search_with_rewrite("search", "退款"))
    try:
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        manager.rewrite_query = AsyncMock(return_value=["退款"])
        assert (await manager.search_with_rewrite("search", "退款")).success
        assert not manager._search_locks._locks
    finally:
        await manager._client.close()


@pytest.mark.asyncio
async def test_pipeline_hit_skips_rerank_and_expiration_recomputes():
    manager = search_manager()
    manager._tools["search"].handler = AsyncMock(return_value=["a", "b", "c"])
    manager._rerank_with_status = AsyncMock(return_value=(["b"], True))
    try:
        assert (await manager.search_with_rewrite("search", "退款", top_k=1)).reranked
        second = await manager.search_with_rewrite("search", "退款", top_k=1)
        assert second.cached and second.reranked and second.data == ["b"]
        assert manager._rerank_with_status.await_count == 1
        for key, (data, expiry, reranked) in list(manager._cache.items()):
            if key.startswith("search:pipeline:"):
                manager._cache[key] = (data, 0, reranked)
        assert not (await manager.search_with_rewrite("search", "退款", top_k=1)).cached
        assert manager._rerank_with_status.await_count == 2
    finally:
        await manager._client.close()


@pytest.mark.asyncio
async def test_usage_is_request_scoped_and_contains_no_prompts():
    async def request(model, tokens):
        client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            usage={"input_tokens": tokens, "output_tokens": 3, "cache_read_input_tokens": 2}))))
        with collect_performance():
            with measure_stage("execution"):
                await asyncio.sleep(0)
                await create_message(client, stage="agent.general", model=model, messages=[{"role":"user", "content":"private prompt"}])
            return performance_snapshot()
    a, b = await asyncio.gather(request("a", 10), request("b", 20))
    assert a["llm"]["input_tokens"] == 10 and b["llm"]["input_tokens"] == 20
    assert a["llm"]["calls"] == b["llm"]["calls"] == 1
    assert "execution" in a["stages_ms"] and "private prompt" not in json.dumps(a)
    assert performance_snapshot() == {}


@pytest.mark.asyncio
async def test_unreported_usage_and_failed_calls_are_visible():
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=[SimpleNamespace(), RuntimeError("private error") ])))
    with collect_performance():
        await create_message(client, model="test")
        with pytest.raises(RuntimeError):
            await create_message(client, model="test")
        snapshot = performance_snapshot()
    assert snapshot["llm"]["calls"] == 2 and snapshot["llm"]["errors"] == 1
    assert snapshot["llm"]["usage_reported_calls"] == 0
    assert "private error" not in json.dumps(snapshot)


@pytest.mark.asyncio
async def test_background_profile_is_throttled_but_preferences_and_force_are_immediate(monkeypatch):
    monkeypatch.setenv("PROFILE_UPDATE_MIN_INTERVAL_S", "60")
    memory = MemoryManager.__new__(MemoryManager)
    memory._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    memory._update_profile = AsyncMock(return_value=True)
    memory._get_working_memory = AsyncMock(return_value=[Message(MsgRole.USER, "退款多久到？")])
    try:
        await memory.update_profile("u", "c", only_if_due=True)
        for _ in range(4):
            await memory.update_profile("u", "c", only_if_due=True)
        assert memory._update_profile.await_count == 1
        memory._get_working_memory.return_value = [Message(MsgRole.USER, "请记住，以后请用简短回答")]
        await memory.update_profile("u", "c", only_if_due=True)
        assert memory._update_profile.await_count == 2
        await memory.update_profile("u", "c")
        assert memory._update_profile.await_count == 3
        monkeypatch.setenv("PROFILE_UPDATE_MIN_INTERVAL_S", "0")
        await memory.update_profile("u", "c", only_if_due=True)
        assert memory._update_profile.await_count == 4
    finally:
        await memory._redis.aclose()


@pytest.mark.asyncio
async def test_profile_failure_does_not_suppress_retry():
    memory = MemoryManager.__new__(MemoryManager)
    memory._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    memory._update_profile = AsyncMock(side_effect=[False, True])
    try:
        await memory.update_profile("u", "c", only_if_due=True)
        await memory.update_profile("u", "c", only_if_due=True)
        assert memory._update_profile.await_count == 2
    finally:
        await memory._redis.aclose()
