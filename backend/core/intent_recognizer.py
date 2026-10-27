"""
亮点：端到端意图识别

三路融合策略：
  1. LLM 语义理解（权重 70%）—— 主力，理解复杂语义和上下文
  2. Embedding 向量相似度（权重 20%）—— 快速匹配常见表达
  3. 关键词模式匹配（权重 10%）—— 零延迟兜底

三路结果通过加权投票合并，置信度低于阈值时降级为 OTHER。
LLM 和 Embedding 并行调用，不串行等待。
"""
import asyncio
import math
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from core.llm_utils import create_message, extract_text_content
from core.locales import DEFAULT_LOCALE, normalize_locale, pick

logger = logging.getLogger(__name__)


class IntentCategory(Enum):
    QUERY      = "query"       # 查询信息
    COMPLAINT  = "complaint"   # 投诉不满
    REQUEST    = "request"     # 请求操作
    GREETING   = "greeting"    # 问候
    ESCALATION = "escalation"  # 要求升级/转人工
    TECHNICAL  = "technical"   # 技术问题
    BILLING    = "billing"     # 账单/退款
    ACCOUNT    = "account"     # 账户管理
    FEEDBACK   = "feedback"    # 正面反馈
    ORDER_STATUS = "order_status"        # 订单状态
    LOGISTICS = "logistics"              # 物流配送
    REFUND = "refund"                    # 退款/退货
    INVOICE = "invoice"                  # 发票
    PAYMENT_ISSUE = "payment_issue"      # 支付/扣款异常
    ACCOUNT_SECURITY = "account_security" # 账户安全
    TECHNICAL_LOGIN = "technical_login"  # 登录认证故障
    TECHNICAL_CRASH = "technical_crash"  # 崩溃/错误码
    HUMAN_HANDOFF = "human_handoff"      # 转人工
    OTHER      = "other"


class UrgencyLevel(Enum):
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4


@dataclass
class IntentResult:
    intent:     IntentCategory
    confidence: float
    urgency:    UrgencyLevel
    intent_group: str
    entities:   Dict[str, List[str]]   # 从消息中提取的实体
    reasoning:  str
    latency_ms: float
    source_scores: Dict[str, float] = field(default_factory=dict)
    embedding_source: str = "local-ngram"


# ── Few-shot 模板（同时用于 LLM 示例和 Embedding 匹配）────────────────────────
_TEMPLATES: Dict[IntentCategory, List[str]] = {
    IntentCategory.QUERY:      ["我的订单状态是什么？", "如何重置密码？", "快递什么时候到？"],
    IntentCategory.COMPLAINT:  ["等了好几个小时！", "服务太差了！", "一直没人处理！"],
    IntentCategory.REQUEST:    ["帮我取消订单", "我需要修改地址", "请协助退款"],
    IntentCategory.GREETING:   ["你好", "嗨，有人吗", "早上好"],
    IntentCategory.ESCALATION: ["我要投诉！", "转人工客服", "找你们经理"],
    IntentCategory.TECHNICAL:  ["应用一直崩溃", "无法登录", "出现500错误"],
    IntentCategory.BILLING:    ["为什么扣了两次款？", "申请退款", "发票问题"],
    IntentCategory.ACCOUNT:    ["修改邮箱", "注销账户", "更新个人信息"],
    IntentCategory.FEEDBACK:   ["服务很棒！", "非常满意", "给个好评"],
    IntentCategory.ORDER_STATUS: ["我的订单现在是什么状态？", "订单有没有发货？", "订单处理到哪一步了？"],
    IntentCategory.LOGISTICS: ["快递什么时候到？", "物流一直不更新", "配送要多久？"],
    IntentCategory.REFUND: ["我要申请退款", "退货退款怎么处理？", "退款多久到账？"],
    IntentCategory.INVOICE: ["帮我开发票", "发票抬头怎么改？", "电子发票在哪里？"],
    IntentCategory.PAYMENT_ISSUE: ["为什么重复扣款？", "支付失败怎么办？", "这个月多扣了钱"],
    IntentCategory.ACCOUNT_SECURITY: ["账户被盗了", "发现异常登录", "我要重置密码"],
    IntentCategory.TECHNICAL_LOGIN: ["登录一直报401", "验证码收不到", "无法登录账号"],
    IntentCategory.TECHNICAL_CRASH: ["应用一直崩溃", "页面报500错误", "系统闪退"],
    IntentCategory.HUMAN_HANDOFF: ["转人工客服", "我要找人工", "请升级处理"],
}

# English templates keep the same categories and coarse/fine grouping.
_TEMPLATES_EN: Dict[IntentCategory, List[str]] = {
    IntentCategory.QUERY:      ["What is my order status?", "How do I reset my password?", "When will my parcel arrive?"],
    IntentCategory.COMPLAINT:  ["I have waited for hours!", "Your service is terrible!", "Nobody has handled my case!"],
    IntentCategory.REQUEST:    ["Please cancel my order", "I need to change my address", "Please help me get a refund"],
    IntentCategory.GREETING:   ["Hello", "Hi, is anyone there?", "Good morning"],
    IntentCategory.ESCALATION: ["I want to file a complaint!", "Transfer me to a human agent", "I need your manager"],
    IntentCategory.TECHNICAL:  ["The app keeps crashing", "I cannot log in", "I am getting a 500 error"],
    IntentCategory.BILLING:    ["Why was I charged twice?", "I want a refund", "I have an invoice problem"],
    IntentCategory.ACCOUNT:    ["Change my email address", "Close my account", "Update my personal information"],
    IntentCategory.FEEDBACK:   ["Great service!", "Very satisfied", "I would like to leave positive feedback"],
    IntentCategory.ORDER_STATUS: ["What is the status of my order now?", "Has my order shipped?", "Which step is my order at?"],
    IntentCategory.LOGISTICS: ["When will my delivery arrive?", "Tracking has not updated", "How long does shipping take?"],
    IntentCategory.REFUND: ["I want to request a refund", "How do I return an item for a refund?", "How long does a refund take?"],
    IntentCategory.INVOICE: ["Please issue an invoice for me", "How do I change the invoice title?", "Where can I find the e-invoice?"],
    IntentCategory.PAYMENT_ISSUE: ["Why was I charged twice?", "What should I do if payment failed?", "There is an extra charge this month"],
    IntentCategory.ACCOUNT_SECURITY: ["My account was hacked", "I noticed an unauthorized login", "I need to reset my password"],
    IntentCategory.TECHNICAL_LOGIN: ["I keep getting 401 when logging in", "I did not receive the verification code", "I cannot sign in to my account"],
    IntentCategory.TECHNICAL_CRASH: ["The app keeps crashing", "The page shows a 500 error", "The system force-closes"],
    IntentCategory.HUMAN_HANDOFF: ["Transfer me to a human agent", "I need a real person", "Please escalate this case"],
}

_SPECIFIC_INTENTS = {
    IntentCategory.ORDER_STATUS,
    IntentCategory.LOGISTICS,
    IntentCategory.REFUND,
    IntentCategory.INVOICE,
    IntentCategory.PAYMENT_ISSUE,
    IntentCategory.ACCOUNT_SECURITY,
    IntentCategory.TECHNICAL_LOGIN,
    IntentCategory.TECHNICAL_CRASH,
    IntentCategory.HUMAN_HANDOFF,
}

_GENERIC_INTENTS = {
    IntentCategory.QUERY,
    IntentCategory.BILLING,
    IntentCategory.TECHNICAL,
    IntentCategory.ACCOUNT,
    IntentCategory.ESCALATION,
}

_INTENT_GROUPS: Dict[IntentCategory, IntentCategory] = {
    IntentCategory.ORDER_STATUS: IntentCategory.QUERY,
    IntentCategory.LOGISTICS: IntentCategory.QUERY,
    IntentCategory.REFUND: IntentCategory.BILLING,
    IntentCategory.INVOICE: IntentCategory.BILLING,
    IntentCategory.PAYMENT_ISSUE: IntentCategory.BILLING,
    IntentCategory.ACCOUNT_SECURITY: IntentCategory.ACCOUNT,
    IntentCategory.TECHNICAL_LOGIN: IntentCategory.TECHNICAL,
    IntentCategory.TECHNICAL_CRASH: IntentCategory.TECHNICAL,
    IntentCategory.HUMAN_HANDOFF: IntentCategory.ESCALATION,
}

# 紧急关键词
_URGENCY_KEYWORDS = {
    UrgencyLevel.CRITICAL: ["紧急", "emergency", "urgent", "asap", "立刻", "immediately"],
    UrgencyLevel.HIGH:     ["今天", "马上", "尽快", "hurry", "now", "right away", "today"],
    UrgencyLevel.MEDIUM:   ["这周", "soon", "快点", "this week"],
}


def _cosine(a: List[float], b: List[float]) -> float:
    """纯 Python 余弦相似度，不依赖 numpy。"""
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class IntentRecognizer:
    """
    端到端意图识别器。

    LLM 使用 Anthropic Messages 客户端，向量使用独立的硅基流动客户端。
    模板向量在首次请求时生成并缓存；在线不可用时整次匹配回退本地 n-gram。
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        confidence_threshold: float = 0.5,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client    = AsyncAnthropic(**kwargs)
        self.model     = model
        self.threshold = confidence_threshold
        from core.intent_embedding import IntentEmbedding
        self._embedding = IntentEmbedding()
        embedding_enabled = os.getenv('INTENT_EMBEDDING_ENABLED', 'true').strip().lower()
        if embedding_enabled not in {'true', 'false', '1', '0', 'yes', 'no', 'on', 'off'}:
            raise ValueError('INTENT_EMBEDDING_ENABLED 必须为 true/false')
        self._embedding_enabled = embedding_enabled in {'true', '1', 'yes', 'on'}
        self._cache: Dict[str, IntentResult] = {}
        self._cache_expiries: Dict[str, float] = {}
        self.cache_hits   = 0
        self.cache_misses = 0

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    async def recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        locale: str = DEFAULT_LOCALE,
    ) -> IntentResult:
        """
        识别用户意图。

        history 格式：[{"role": "user"/"assistant", "content": "..."}]
        locale 只影响系统生成的提示词、模板和说明，不用于猜测用户文本语言。
        """
        locale = normalize_locale(locale)
        key = self._cache_key(message, history, locale)
        if key in self._cache and time.monotonic() < self._cache_expiries.get(key, 0):
            self.cache_hits += 1
            return self._cache[key]
        self.cache_misses += 1

        t0 = time.monotonic()

        # LLM 和向量分支并行；provider=local 仍参与向量投票。
        llm_task = asyncio.create_task(self._llm_recognize(message, history, locale))
        emb_task = asyncio.create_task(self._embedding_recognize(message, locale)) if self._embedding_enabled else None
        pat      = self._pattern_recognize(message)

        if emb_task:
            llm, emb = await asyncio.gather(llm_task, emb_task)
        else:
            llm = await llm_task
            emb = {"intent": IntentCategory.OTHER, "confidence": 0.0, "source": "disabled"}

        intent, confidence, source_scores = self._vote(llm, emb, pat)
        entities = self._extract_entities(message)
        urgency  = self._urgency(message, intent)

        result = IntentResult(
            intent=intent,
            confidence=confidence,
            urgency=urgency,
            intent_group=self._intent_group(intent),
            entities=entities,
            reasoning=llm.get("reasoning", ""),
            latency_ms=(time.monotonic() - t0) * 1000,
            source_scores=source_scores,
            embedding_source=emb.get("source", "unavailable"),
        )

        # LRU 缓存
        if len(self._cache) >= 1000:
            for k in list(self._cache)[:500]:
                del self._cache[k]
                self._cache_expiries.pop(k, None)
        self._cache[key] = result
        self._cache_expiries[key] = time.monotonic() + (60 if result.embedding_source == "local-ngram" and self._embedding.enabled else 300)
        return result

    async def check_embedding(self, message: str, locale: str = DEFAULT_LOCALE) -> Dict[str, Any]:
        """独立验证向量链路；不调用 LLM，也不写入意图识别结果缓存。"""
        if not self._embedding_enabled:
            return {'success': False, 'source': 'disabled', 'error': 'embedding_branch_disabled'}
        started = time.monotonic()
        result = await self._embedding_recognize(message, locale)
        source = result.get('source', 'unavailable')
        return {
            'success': source == 'siliconflow/' + self._embedding.MODEL,
            'source': source,
            'intent': result['intent'].value,
            'confidence': result['confidence'],
            'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
            'status': self._embedding.status(),
        }

    def learn(self, message: str, correct: IntentCategory) -> None:
        """在线学习：将纠正样本加入模板，清除对应 Embedding 缓存。"""
        tpls = _TEMPLATES.setdefault(correct, [])
        if message not in tpls:
            tpls.append(message)
            self._embedding.remote_templates.clear()
            self._embedding.local_templates.clear()
            self._cache.clear()  # 模板更新后旧缓存可能对应过时结果
            self._cache_expiries.clear()
            logger.info(f"学习新样本 → {correct.value}: {message[:40]}")

    # ── 三路识别策略 ──────────────────────────────────────────────────────────

    async def _llm_recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
        locale: str = DEFAULT_LOCALE,
    ) -> Dict[str, Any]:
        """策略 1：LLM 语义理解（Few-shot + 上下文）。"""
        message = self._clean_text(message)
        templates = _TEMPLATES_EN if locale == "en" else _TEMPLATES
        if locale == "en":
            examples = "\n".join(
                f'  Message: "{t}" → intent: {cat.value}'
                for cat, tpls in templates.items()
                for t in tpls[:1]
            )
            ctx = ""
            if history:
                ctx = "\nRecent conversation:\n" + "\n".join(
                    f"  {self._clean_text(m.get('role', 'user'))}: {self._clean_text(m.get('content', ''))}"
                    for m in history[-3:]
                )
            prompt = f"""You are a customer support intent analyst. Classify the user's intent from the examples and return JSON.
If the message matches a specific business intent, prefer the specific intent over the broad category.
For example, prefer refund over billing, invoice over billing, and technical_login over technical.

Examples:
{examples}

        {ctx}
        User message: "{message}"

Return format (JSON only, no other text):
{{"intent": "<intent value>", "confidence": <0-1>, "reasoning": "<one sentence>"}}

Available intents: {", ".join(c.value for c in IntentCategory)}"""
        else:
            examples = "\n".join(
                f'  消息: "{t}" → 意图: {cat.value}'
                for cat, tpls in templates.items()
                for t in tpls[:1]
            )
            ctx = ""
            if history:
                ctx = "\n最近对话:\n" + "\n".join(
                    f"  {self._clean_text(m.get('role', 'user'))}: {self._clean_text(m.get('content', ''))}"
                    for m in history[-3:]
                )
            prompt = f"""你是客服意图分析专家。根据示例判断用户意图，返回 JSON。
如果用户问题能匹配细粒度业务意图，请优先返回细粒度意图，而不是宽泛大类。
例如退款优先返回 refund，发票优先返回 invoice，登录故障优先返回 technical_login。

示例:
{examples}

        {ctx}
        用户消息: "{message}"

返回格式（仅 JSON，不要其他文字）:
{{"intent": "<意图值>", "confidence": <0-1>, "reasoning": "<一句话说明>"}}

可选意图: {", ".join(c.value for c in IntentCategory)}"""
        prompt = self._clean_text(prompt)

        try:
            resp = await asyncio.wait_for(create_message(self.client,
                stage="intent",
                model=self.model,
                max_tokens=256,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            ), timeout=max(0.1, float(os.getenv("ECHOMIND_INTENT_TIMEOUT_S", "8"))))
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
            try:
                data["intent"] = IntentCategory(data["intent"])
            except ValueError:
                data["intent"] = IntentCategory.OTHER
            confidence = data.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("intent confidence must be a finite number between 0 and 1")
            return data
        except Exception as ex:
            logger.warning(f"LLM 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0,
                    "reasoning": pick(locale, "LLM failed", "LLM 失败"), "failed": True}

    async def _embedding_recognize(self, message: str, locale: str = DEFAULT_LOCALE) -> Dict[str, Any]:
        """策略 2：Embedding 向量相似度匹配。"""
        try:
            templates = _TEMPLATES_EN if locale == "en" else _TEMPLATES
            templates, msg_vec, source = await self._embedding.vectors(
                self._clean_text(message), templates, self._local_embedding)

            best_cat, best_score = IntentCategory.OTHER, 0.0
            for cat, vecs in templates.items():
                score = max(_cosine(msg_vec, v) for v in vecs)
                if score > best_score:
                    best_score, best_cat = score, cat

            return {"intent": best_cat, "confidence": min(1.0, max(0.0, best_score)), "source": source}
        except Exception as ex:
            logger.warning(f"Embedding 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0, "source": "unavailable"}

    def _pattern_recognize(self, message: str) -> Dict[str, Any]:
        """策略 3：关键词模式匹配（同步，零延迟兜底）。"""
        msg = message.lower()
        specific_patterns = {
            IntentCategory.HUMAN_HANDOFF: ["转人工", "人工客服", "找人工", "human agent", "real person", "representative", "human support"],
            IntentCategory.ORDER_STATUS: ["订单状态", "发货了吗", "处理到哪", "order status", "where is my order", "has my order shipped", "shipped yet"],
            IntentCategory.LOGISTICS: ["物流", "快递", "配送", "运单", "delivery", "shipping", "tracking", "courier", "delayed", "late"],
            IntentCategory.REFUND: ["退款", "退货", "refund", "return", "refunded", "refunding", "returned", "returning", "money back"],
            IntentCategory.INVOICE: ["发票", "抬头", "税号", "invoice", "invoices", "receipt", "tax id"],
            IntentCategory.PAYMENT_ISSUE: ["重复扣款", "多扣", "支付失败", "扣费", "payment failed", "charged twice", "double charge", "duplicate charge", "overcharged", "charged", "charging"],
            IntentCategory.ACCOUNT_SECURITY: ["被盗", "异常登录", "重置密码", "两步验证", "安全", "hacked", "unauthorized login", "reset password", "two-factor", "2fa", "security", "compromised"],
            IntentCategory.TECHNICAL_LOGIN: ["无法登录", "登录失败", "401", "验证码", "cannot log in", "can't log in", "login failed", "verification code", "sign in", "locked out", "cannot sign in", "can't sign in"],
            IntentCategory.TECHNICAL_CRASH: ["崩溃", "闪退", "500", "报错", "crash", "crashes", "crashing", "crashed", "500 error"],
        }
        generic_patterns = {
            IntentCategory.ESCALATION: ["投诉", "经理", "supervisor", "manager", "complain", "complaint", "complaining", "escalate"],
            IntentCategory.COMPLAINT:  ["太差", "糟糕", "horrible", "等了很久", "terrible", "awful", "worst", "waited hours"],
            IntentCategory.QUERY:      ["?", "？", "怎么", "什么", "status", "how", "what", "when", "where is", "how long"],
            IntentCategory.REQUEST:    ["帮我", "需要", "please", "help", "help me", "i need", "cancel", "cancelled", "canceled", "cancelling", "canceling"],
            IntentCategory.GREETING:   ["你好", "嗨", "hello", "hi", "good morning"],
            IntentCategory.BILLING:    ["退款", "扣款", "发票", "refund", "billing", "charge", "charges", "invoices"],
            IntentCategory.TECHNICAL:  ["崩溃", "报错", "error", "crash", "bug", "not working", "failing", "broken"],
            IntentCategory.ACCOUNT:    ["密码", "邮箱", "账户", "password", "email", "account", "profile"],
        }

        best_cat, best_score = self._best_pattern_match(msg, specific_patterns)
        if best_cat != IntentCategory.OTHER:
            return {"intent": best_cat, "confidence": best_score}

        best_cat, best_score = self._best_pattern_match(msg, generic_patterns)
        return {"intent": best_cat, "confidence": best_score}

    # ── 投票合并 ──────────────────────────────────────────────────────────────

    def _vote(self, llm: Dict, emb: Dict, pat: Dict) -> tuple[IntentCategory, float, Dict[str, float]]:
        """加权投票。返回最终意图、融合置信度和各路来源得分。"""
        cleaned = []
        for source in (llm, emb, pat):
            value = source.get("confidence")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                source = {"intent": IntentCategory.OTHER, "confidence": 0.0, "failed": True}
            cleaned.append(source)
        llm, emb, pat = cleaned
        source_scores = {
            "llm": float(llm.get("confidence", 0.0) or 0.0),
            "embedding": float(emb.get("confidence", 0.0) or 0.0),
            "pattern": float(pat.get("confidence", 0.0) or 0.0),
        }
        if llm.get("failed"):
            if emb.get("intent") != IntentCategory.OTHER and emb.get("confidence", 0.0) > 0:
                return emb["intent"], source_scores["embedding"], source_scores
            if pat.get("intent") != IntentCategory.OTHER and pat.get("confidence", 0.0) > 0:
                return pat["intent"], source_scores["pattern"], source_scores
            return IntentCategory.OTHER, 0.0, source_scores

        if self._embedding_enabled:
            weights = [(llm, 0.7), (emb, 0.2), (pat, 0.1)]
        else:
            weights = [(llm, 0.85), (pat, 0.15)]
        scores: Dict[IntentCategory, float] = {}
        group_scores: Dict[IntentCategory, float] = {}
        for result, w in weights:
            cat  = result.get("intent", IntentCategory.OTHER)
            conf = result.get("confidence", 0.0)
            weighted_score = w * conf
            scores[cat] = scores.get(cat, 0.0) + weighted_score
            group = _INTENT_GROUPS.get(cat, cat)
            group_scores[group] = group_scores.get(group, 0.0) + weighted_score

        best_group = max(group_scores, key=group_scores.get)
        best_score = group_scores[best_group]
        best = max(
            (cat for cat in scores if _INTENT_GROUPS.get(cat, cat) == best_group),
            key=scores.get,
        )
        pat_intent = pat.get("intent", IntentCategory.OTHER)
        pat_conf = float(pat.get("confidence", 0.0) or 0.0)
        if (best in _GENERIC_INTENTS and pat_intent in _SPECIFIC_INTENTS
                and _INTENT_GROUPS.get(pat_intent, pat_intent) == best_group
                and pat_conf >= 0.5 and best_score < 0.8):
            source_scores["refined_by_pattern"] = pat_conf
            return pat_intent, max(best_score, pat_conf), source_scores
        if best_score < self.threshold:
            return IntentCategory.OTHER, best_score, source_scores
        return best, best_score, source_scores

    # ── 实体提取 ──────────────────────────────────────────────────────────────

    def _extract_entities(self, message: str) -> Dict[str, List[str]]:
        """用规则提取高价值实体，避免每次识别都额外调用 LLM。"""
        message = self._clean_text(message)
        order_ids: List[str] = []
        for pattern in (
            # Explicit hash form: #12345 / #ABC-123.
            r"#\s*([A-Za-z0-9][A-Za-z0-9_-]{3,31})",
            # "order is <token>" with a digit; keeps words such as "pending" out.
            r"(?<![a-z])order\s+is\s+(?=[A-Za-z0-9_-]*\d)"
            r"([A-Za-z0-9][A-Za-z0-9_-]{3,31})",
            # Label form; the left boundary prevents "border"/"recorder".
            r"(?:订单号?|(?<![a-z])order\s*(?:id|number|no\.?))\s*(?:is|为|是)?\s*[:：#]?\s*"
            r"(?=[A-Za-z0-9_-]*\d)([A-Za-z0-9][A-Za-z0-9_-]{3,31})",
            # Explicit separator.
            r"(?<![a-z])order\s*[:：#]\s*(?=[A-Za-z0-9_-]*\d)([A-Za-z0-9][A-Za-z0-9_-]{3,31})",
            # Bare numeric or alphanumeric token containing a digit; exclude 3-digit HTTP codes.
            r"(?<![a-z])order\s+(\d[A-Za-z0-9_-]{3,31})",
            r"(?<![a-z])order\s+(?!(?:[45]\d{2})(?![A-Za-z0-9_-]))"
            r"([A-Za-z]{0,10}\d[A-Za-z0-9_-]{2,31})",
        ):
            order_ids.extend(re.findall(pattern, message, re.I))
        return {
            "order_id": self._unique(order_ids),
            "product": [],
            "date": self._unique(re.findall(
                r"(今天|明天|昨天|本周|这周|下周|\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?"
                r"|today|tomorrow|yesterday|this\s+week|next\s+week"
                r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2})",
                message, re.I)),
            "amount": self._unique(re.findall(
                r"((?:¥|￥|\$)\s*\d+(?:\.\d{1,2})?"
                r"|\d+(?:\.\d{1,2})?\s*(?:元|块|rmb|cny|usd|nzd|dollars?|美元))",
                message, re.I)),
            "error_code": self._unique(
                re.findall(r"(?:error(?:_code)?|错误码|状态码|status\s*code|http)\s*[:：#]?\s*([45]\d{2})\b", message, re.I)
                + re.findall(r"\b([45]\d{2})\b", message)
            ),
        }

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _local_embedding(text: str, dims: int = 256) -> List[float]:
        """稳定的字符 n-gram 哈希向量，用于无远端 Embedding 时的语义近似匹配。"""
        normalized = text.lower().strip()
        vec = [0.0] * dims
        tokens = set()
        for n in (1, 2, 3):
            if len(normalized) >= n:
                tokens.update(normalized[i:i + n] for i in range(len(normalized) - n + 1))
        if not tokens:
            tokens.add(normalized)

        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return vec

    def _urgency(self, message: str, intent: IntentCategory) -> UrgencyLevel:
        msg = message.lower()
        msg = re.sub(r"(?:不(?:是|用|需要)?|无需|无须|非|并不|没那么|暂不)\s*(?:紧急|立刻|马上|尽快|快点)", "", msg)
        msg = re.sub(r"\b(?:not|isn't|is not|no)\s+(?:an?\s+)?(?:emergency|urgent|asap|now)\b", "", msg)
        for level, kws in _URGENCY_KEYWORDS.items():
            if any(re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", msg) if kw.isascii() else kw in msg for kw in kws):
                return level
        if intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF):
            return UrgencyLevel.HIGH
        if intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.MEDIUM
        return UrgencyLevel.LOW

    def _cache_key(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        locale: str = DEFAULT_LOCALE,
    ) -> str:
        payload = {"message": self._clean_text(message), "locale": locale}
        if history:
            payload["history"] = [
                {
                    "role": self._clean_text(item.get("role", ""))[:20],
                    "content": self._clean_text(item.get("content", "")),
                }
                for item in history[-3:]
            ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _unique(values: List[str]) -> List[str]:
        return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))

    @staticmethod
    def _best_pattern_match(
        message: str,
        patterns: Dict[IntentCategory, List[str]],
    ) -> tuple[IntentCategory, float]:
        best_cat, best_score = IntentCategory.OTHER, 0.0
        for cat, kws in patterns.items():
            hits = sum(1 for kw in kws if (
                re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", message)
                if kw.isascii() else kw in message
            ))
            if not hits:
                continue
            # 单个明确业务关键词就给可用置信度；多个关键词命中时提高置信度。
            score = min(1.0, 0.5 + 0.25 * (hits - 1))
            if score > best_score:
                best_score, best_cat = score, cat
        return best_cat, best_score

    @staticmethod
    def _intent_group(intent: IntentCategory) -> str:
        return _INTENT_GROUPS.get(intent, intent).value

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 HTTP 客户端编码 prompt 时崩溃。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @property
    def cache_stats(self) -> Dict[str, Any]:
        total = self.cache_hits + self.cache_misses
        return {
            "size": len(self._cache),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "hit_rate": self.cache_hits / total if total else 0.0,
        }
