"""轻量语言切换的针对性测试：locale 契约、英文输入、Skills 触发、跨语检索与固定反馈。"""
import asyncio
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx

from core.intent_recognizer import IntentCategory, IntentRecognizer
from core.locales import DEFAULT_LOCALE, LocaleError, normalize_locale, pick
from core.concurrency import KeyedLocks
from core.skill_loader import SkillManager
from agents.agent_orchestrator import (
    AgentOrchestrator,
    AgentResponse,
    AgentType,
    BillingAgent,
    EscalationAgent,
    GeneralAgent,
    Request,
    ResponseComposer,
    TechnicalAgent,
)
from core.intent_recognizer import UrgencyLevel
import api.main as api
from monitor.performance_monitor import Alert, PerformanceMonitor, Severity, Suggestion
from tooling.tool_manager import MCPToolManager, Tool, _bridge_boost


def test_locale_values_default_and_reject_unsupported_values():
    assert normalize_locale(None) == DEFAULT_LOCALE
    assert normalize_locale("") == DEFAULT_LOCALE
    assert normalize_locale("en") == "en"
    assert normalize_locale("zh-CN") == "zh-CN"
    for invalid in ("fr", "EN", "zh", 1, True):
        with pytest.raises(LocaleError):
            normalize_locale(invalid)
    assert pick("en", "English", "中文") == "English"
    assert pick("zh-CN", "English", "中文") == "中文"


def test_english_pattern_and_entity_extraction():
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    assert recognizer._pattern_recognize("I keep getting a 401 when I log in", "en")["intent"] is IntentCategory.TECHNICAL_LOGIN
    assert recognizer._pattern_recognize("I was charged twice this month", "en")["intent"] is IntentCategory.PAYMENT_ISSUE
    assert recognizer._pattern_recognize("Please transfer me to a human agent", "en")["intent"] is IntentCategory.HUMAN_HANDOFF
    assert recognizer._pattern_recognize("Please issue an invoice for me", "en")["intent"] is IntentCategory.INVOICE
    entities = recognizer._extract_entities("My order #12345 was charged $99.50 today with error 401", "en")
    assert entities["order_id"] == ["12345"]
    assert entities["error_code"] == ["401"]
    assert entities["amount"] and entities["amount"][0].startswith("$")
    assert entities["date"] == ["today"]


def test_english_order_id_extraction_does_not_capture_ordinary_words():
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    assert recognizer._extract_entities("My order number is 12345", "en")["order_id"] == ["12345"]
    assert recognizer._extract_entities("My order is 12345", "en")["order_id"] == ["12345"]
    assert recognizer._extract_entities("My order is ABC-123", "en")["order_id"] == ["ABC-123"]
    assert recognizer._extract_entities("I placed an order yesterday", "en")["order_id"] == []
    assert recognizer._extract_entities("order status", "en")["order_id"] == []
    assert recognizer._extract_entities("order number is pending", "en")["order_id"] == []
    assert recognizer._extract_entities("order no. ABC123", "en")["order_id"] == ["ABC123"]
    assert recognizer._extract_entities("border 12345", "en")["order_id"] == []
    assert recognizer._extract_entities("recorder 12345", "en")["order_id"] == []
    http_code = recognizer._extract_entities("order 500 error", "en")
    assert http_code["order_id"] == []
    assert http_code["error_code"] == ["500"]
    assert recognizer._extract_entities("订单号是 12345", "en")["order_id"] == ["12345"]
    assert recognizer._extract_entities("订单状态", "en")["order_id"] == []


def test_english_inflected_forms_reach_pattern_fallback():
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    assert recognizer._pattern_recognize("The app keeps crashing", "en")["intent"] is IntentCategory.TECHNICAL_CRASH
    assert recognizer._pattern_recognize("I returned the item and want a refund", "en")["intent"] is IntentCategory.REFUND
    assert recognizer._pattern_recognize("Please cancel my order", "en")["intent"] is IntentCategory.REQUEST
    assert recognizer._pattern_recognize("My order is delayed", "en")["intent"] is IntentCategory.LOGISTICS
    assert recognizer._pattern_recognize("I am locked out of my account", "en")["intent"] is IntentCategory.TECHNICAL_LOGIN
    assert recognizer._pattern_recognize("My account was compromised", "en")["intent"] is IntentCategory.ACCOUNT_SECURITY


def test_chinese_rule_path_preserves_legacy_entities_and_mixed_text_routing():
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    assert recognizer._extract_entities("请查订单号ABCD的物流")["order_id"] == ["ABCD"]
    assert recognizer._extract_entities("我的order_id:12345什么时候到")["order_id"] == ["12345"]
    assert recognizer._extract_entities("订单号A123-BC4需要退款")["order_id"] == ["A123-BC4"]
    assert recognizer._pattern_recognize("我的账户 token 无法使用")["intent"] is IntentCategory.ACCOUNT
    assert recognizer._pattern_recognize("我需要客服 human agent")["intent"] is IntentCategory.REQUEST
    assert recognizer._urgency("请今天处理退款", IntentCategory.REFUND) is UrgencyLevel.HIGH
    assert recognizer._urgency("我的订单 today 到吗", IntentCategory.QUERY) is UrgencyLevel.LOW


def test_english_intent_prompt_uses_english_examples_without_guessing_from_text():
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    recognizer.model = "test"
    create = AsyncMock(return_value=SimpleNamespace(
        content=[{"type": "text", "text": '{"intent":"refund","confidence":0.9}'}]))
    recognizer.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    result = asyncio.run(recognizer._llm_recognize("I want a refund", None, "en"))
    assert result["intent"] is IntentCategory.REFUND
    prompt = create.call_args.kwargs["messages"][0]["content"]
    assert 'Message: "I want to request a refund"' in prompt
    assert "Available intents" in prompt


def test_intent_cache_is_isolated_by_locale():
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    assert recognizer._cache_key("退款", None, "zh-CN") != recognizer._cache_key("退款", None, "en")


def test_agent_keeps_chinese_rules_and_adds_english_output_requirement():
    agent = GeneralAgent(SimpleNamespace(), "test-model")
    zh = agent._build_system_prompt(Request("你好", "u", "c", locale="zh-CN"))
    en = agent._build_system_prompt(Request("hello", "u", "c", locale="en"))
    assert "角色契约" in zh and "角色契约" in en
    assert "Role contract" not in en
    assert "输出语言要求" not in zh
    assert "输出语言要求" in en and "英语" in en


class FakeSkillManager:
    def prompt_for(self, message, agent_type, locale="zh-CN"):
        return "### 中文技能\n不得承诺退款。"


def test_agent_language_requirement_is_appended_after_skills():
    agent = GeneralAgent(SimpleNamespace(), "test-model", skill_manager=FakeSkillManager())
    prompt = agent._build_system_prompt(Request("login failed 401", "u", "c", locale="en"))
    assert "[动态 Skills]" in prompt
    assert prompt.index("[动态 Skills]") < prompt.index("[输出语言要求]")
    assert prompt.rstrip().endswith("不要篡改证据。")
    zh = agent._build_system_prompt(Request("登录失败 401", "u", "c", locale="zh-CN"))
    assert "输出语言要求" not in zh


def test_chinese_skill_and_escalation_rules_ignore_english_only_terms():
    root = Path(__file__).parent.parent / "skills"
    manager = SkillManager(str(root))
    manager.load()
    assert manager.prompt_for("我的账户 charge 有问题", "billing", locale="zh-CN") == ""
    assert "账单退款处理规范" in manager.prompt_for(
        "我的账户 charge 有问题", "billing", locale="en")
    agent = GeneralAgent(SimpleNamespace(), "test-model")
    assert not agent._needs_escalation("human agent 是一种客服称呼。", "zh-CN")
    assert agent._needs_escalation("human agent can take over.", "en")


def test_composer_language_requirement_is_appended_after_skills():
    captured = []

    class Client:
        def __init__(self):
            self.messages = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            captured.append(kwargs["messages"][0]["content"])
            return SimpleNamespace(content=[{"type": "text", "text": "English reply"}])

    composer = ResponseComposer(Client(), "test-model", FakeSkillManager())
    req = Request("composite", "u", "c", intent=IntentCategory.TECHNICAL, locale="en")
    responses = [
        AgentResponse(AgentType.TECHNICAL, "Check the token.", True),
        AgentResponse(AgentType.BILLING, "Provide both charge times.", True),
    ]
    result = asyncio.run(composer.compose_result(req, responses))
    assert result.content == "English reply"
    assert captured[0].index("[通用客服输出边界]") < captured[0].index("[输出语言要求]")
    assert "英语回复" in captured[0]


def test_chinese_composer_keeps_original_output_instruction_and_length():
    captured = []

    class Client:
        async def create(self, **kwargs):
            captured.append(kwargs["messages"][0]["content"])
            return SimpleNamespace(content=[{"type": "text", "text": "已处理"}])

        def __init__(self):
            self.messages = SimpleNamespace(create=self.create)

    composer = ResponseComposer(Client(), "test-model", FakeSkillManager())
    req = Request("登录失败，还被重复扣款", "u", "c", locale="zh-CN")
    responses = [AgentResponse(AgentType.BILLING, "先核验账单。", True),
                 AgentResponse(AgentType.TECHNICAL, "检查登录态。", True)]
    asyncio.run(composer.compose_result(req, responses))
    assert "保留必要的排查步骤、核验字段和升级边界。只输出给用户看的中文回复，不要提及 Agent。" in captured[0]
    assert "默认正文约 300–500 个中文字符，分清各问题的结论和下一步" in captured[0]
    assert "[输出语言要求]" not in captured[0]


def test_chinese_mixed_terms_do_not_add_supporting_agents():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [object()],
        AgentType.BILLING: [object()],
        AgentType.TECHNICAL: [object()],
    }
    orchestrator._monitor_degraded = lambda agent: False
    for message, intent, expected in (
        ("我的账户 token 无法使用", IntentCategory.ACCOUNT, AgentType.BILLING),
        ("订单 payment 状态在哪里", IntentCategory.ORDER_STATUS, AgentType.GENERAL),
    ):
        decision = orchestrator._route_decision(Request(
            message, "u", "c", intent=intent, urgency=UrgencyLevel.LOW,
            intent_confidence=.8, locale="zh-CN"))
        assert decision.primary_agent is expected
        assert decision.supporting_agents == []
        assert decision.reason.startswith("intent=")


def test_english_agent_scaffolding_acks_are_english():
    captured = []

    class Client:
        def __init__(self):
            self.messages = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            captured.append(kwargs["messages"])
            return SimpleNamespace(content=[{"type": "text", "text": "English reply"}],
                                   stop_reason="end_turn")

    agent = GeneralAgent(Client(), "test-model")
    req = Request("hello", "u", "c", context="背景", locale="en",
                  intent=IntentCategory.QUERY, intent_group="query",
                  urgency=UrgencyLevel.LOW, intent_confidence=.9)
    result = asyncio.run(agent.handle(req))
    assert result.success
    assistants = [message["content"] for message in captured[0] if message["role"] == "assistant"]
    assert assistants and all("Understood" in content for content in assistants)


def test_english_clarification_is_english():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._recent_tool_traces = deque(maxlen=10)
    req = Request("something vague", "u", "c", intent=IntentCategory.OTHER,
                  intent_group="other", urgency=UrgencyLevel.LOW,
                  intent_confidence=.2, locale="en")
    result = asyncio.run(orchestrator.run(req))
    assert result.response.startswith("I am not sure")
    assert "Low-confidence OTHER" in result.routing_reason


def test_english_escalation_and_agent_failure_are_english():
    escalation = EscalationAgent(SimpleNamespace(), "test-model")
    escalated = asyncio.run(escalation.handle(Request(
        "human", "u", "c", intent=IntentCategory.HUMAN_HANDOFF,
        urgency=UrgencyLevel.CRITICAL, locale="en")))
    assert "human escalation" in escalated.content
    assert "人工" not in escalated.content

    class FailingClient:
        def __init__(self):
            self.messages = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            raise RuntimeError("provider down")

    failed = asyncio.run(GeneralAgent(FailingClient(), "test-model").handle(
        Request("hello", "u", "c", locale="en")))
    assert failed.status == "failed"
    assert "Sorry, something went wrong" in failed.content


def test_english_composer_fallback_is_english():
    class FailingClient:
        def __init__(self):
            self.messages = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            raise RuntimeError("provider down")

    composer = ResponseComposer(FailingClient(), "test-model")
    req = Request("composite", "u", "c", intent=IntentCategory.TECHNICAL, locale="en")
    responses = [
        AgentResponse(AgentType.TECHNICAL, "Check the token.", True),
        AgentResponse(AgentType.BILLING, "Provide both charge times.", True),
    ]
    result = asyncio.run(composer.compose_result(req, responses))
    assert result.content.startswith("Check the token.")
    assert "Additional details:" in result.content


def test_english_messages_trigger_the_existing_chinese_skills():
    root = Path(__file__).parent.parent / "skills"
    manager = SkillManager(str(root))
    manager.load()
    assert manager.errors == []
    technical = manager.prompt_for("I keep getting 401 and cannot log in", "technical", locale="en")
    billing = manager.prompt_for("I was charged twice, please refund me", "billing", locale="en")
    assert "技术支持处理规范" in technical and "研发" not in technical
    assert "账单退款处理规范" in billing
    assert "Technical Support Guidelines" not in technical


def test_english_rewrite_bridges_to_chinese_knowledge_base():
    manager = MCPToolManager("test")
    calls = []

    async def create(**kwargs):
        calls.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(content=[{"type": "text", "text": '["退款政策", "退款到账时间", "退货流程"]'}])

    manager._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    manager._model = "test"
    result = asyncio.run(manager.rewrite_query("How long does a refund take?", n=3, locale="en"))
    assert "中文搜索子查询" in calls[0]
    assert "How long does a refund take?" in result
    assert "退款政策" in result
    assert any("退款" in item for item in result)


def test_english_rewrite_keeps_a_deterministic_term_bridge_when_the_model_fails():
    manager = MCPToolManager("test")

    async def fail(**kwargs):
        raise RuntimeError("provider down")

    manager._client = SimpleNamespace(messages=SimpleNamespace(create=fail))
    manager._model = "test"
    result = asyncio.run(manager.rewrite_query("I was charged twice, please refund me", n=3, locale="en"))
    assert result[0] == "I was charged twice, please refund me"
    assert "重复扣款" in result[-1]
    assert "退款" in result[-1]
    address = asyncio.run(manager.rewrite_query("How do I change my address?", n=3, locale="en"))
    assert "地址" in address[-1] and "修改" in address[-1]


def test_bridge_boost_reorders_chinese_evidence_when_rerank_is_unavailable():
    items = [
        {"title": "演示资料：订单查询", "content": "订单状态"},
        {"title": "演示资料：退款政策", "content": "退款政策 退款 到账"},
        {"title": "演示资料：配送说明", "content": "配送 地址"},
    ]
    refund = _bridge_boost("How long does a refund take?", items)
    assert refund[0]["title"] == "演示资料：退款政策"
    address = _bridge_boost("How do I change my address?", items)
    assert address[0]["title"] == "演示资料：配送说明"


def test_pipeline_cache_is_isolated_by_locale():
    manager = MCPToolManager.__new__(MCPToolManager)
    manager._client = SimpleNamespace()
    manager._model = "test"
    manager._cache = {}
    manager._cache_versions = {}
    manager._search_locks = KeyedLocks()

    async def retrieve(params, context):
        return [{"content": "退款政策", "document_id": "refund"}]

    manager._tools = {"search": Tool("search", "", retrieve, {"type": "object"}, cache_ttl=30)}
    manager.rewrite_query = AsyncMock(return_value=["退款"])

    async def run():
        await manager.search_with_rewrite("search", "refund", locale="en")
        await manager.search_with_rewrite("search", "refund", locale="zh-CN")

    asyncio.run(run())
    assert manager.rewrite_query.await_count == 2


def test_english_composite_request_keeps_primary_and_supporting_agents():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    client = SimpleNamespace()
    orchestrator._pool = {
        AgentType.GENERAL: [GeneralAgent(client, "test")],
        AgentType.TECHNICAL: [TechnicalAgent(client, "test")],
        AgentType.BILLING: [BillingAgent(client, "test")],
    }
    req = Request(
        "Login returns 401 and I was charged twice this month",
        "u", "c",
        intent=IntentCategory.TECHNICAL_LOGIN,
        intent_group="technical",
        urgency=UrgencyLevel.MEDIUM,
        intent_confidence=.9,
        entities={"error_code": ["401"]},
        locale="en",
    )
    decision = orchestrator._route_decision(req)
    assert decision.primary_agent is AgentType.TECHNICAL
    assert AgentType.BILLING in decision.supporting_agents
    assert decision.multi_agent is True


def test_monitor_summary_localizes_fixed_feedback_but_keeps_thresholds():
    monitor = PerformanceMonitor.__new__(PerformanceMonitor)
    monitor._orchestrator = SimpleNamespace(get_stats=lambda: {})
    monitor._tool_manager = SimpleNamespace(get_stats=lambda: {})
    monitor._alerts = [Alert(Severity.WARNING, "tool_success_rate:knowledge_search",
                             "工具成功率告警", 0.5, 0.95)]
    monitor._suggestions = [Suggestion("工具 knowledge_search 连续失败 3 次", "detail", "action", 9,
                                       kind="tool_failures",
                                       params={"tool": "knowledge_search", "fails": 3,
                                               "success_rate": 0.5, "avg_ms": 10,
                                               "circuit_state": "open"})]
    summary = monitor.summary("en")
    assert summary["locale"] == "en"
    assert "Tool knowledge_search failed 3 times" in summary["suggestions"][0]["title"]
    assert "threshold 0.95" in summary["active_alerts"][0]["message"]
    assert summary["active_alerts"][0]["threshold"] == 0.95


@pytest.mark.asyncio
async def test_search_endpoint_passes_locale_and_rejects_invalid_value(monkeypatch):
    manager = SimpleNamespace(search_with_rewrite=AsyncMock(return_value=SimpleNamespace(
        data=[{"title": "演示资料：退款政策"}], reranked=False, cached=False)))
    monkeypatch.setattr(api, "_tool_manager", manager)
    result = await api.search(query="refund", top_k=3, locale="en")
    assert result["locale"] == "en"
    assert manager.search_with_rewrite.await_args.kwargs["locale"] == "en"
    with pytest.raises(Exception) as invalid:
        await api.search(query="refund", top_k=3, locale="fr")
    assert getattr(invalid.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_monitor_endpoint_rejects_invalid_locale(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(api, "_monitor", SimpleNamespace())
    with pytest.raises(HTTPException) as error:
        await api.monitor_summary(locale="fr")
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_chat_not_ready_is_localized_and_invalid_locale_is_400(monkeypatch):
    monkeypatch.setattr(api, "_orchestrator", None)
    monkeypatch.setattr(api, "_memory", None)
    monkeypatch.setattr(api, "_monitor", None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                                 base_url="http://test") as client:
        english = await client.post("/chat", json={"message": "hi", "locale": "en"})
        assert english.status_code == 503
        assert english.json()["detail"] == "Service is not ready"
        chinese = await client.post("/chat", json={"message": "hi"})
        assert chinese.status_code == 503
        assert chinese.json()["detail"] == "服务未就绪"
        invalid = await client.post("/chat", json={"message": "hi", "locale": "fr"})
        assert invalid.status_code == 400
