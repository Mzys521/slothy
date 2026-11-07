"""Evidence-backed memory learning and profile projection, separate from the online graph.
/ 以证据为依据的记忆学习与画像投影，与在线图分离。"""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.infrastructure.conversations import SQLiteConversationStore
from app.infrastructure.workspace import WorkspaceStore

logger = logging.getLogger(__name__)
REMEMBER = re.compile(
    r"^(?:(?:请|帮我)\s*)?记住(?:[：:，,\s]+|"
    r"(?=我|以后|今后|在|这个|当前|前天|昨天|今天|上周|这次|\d{4}))|"
    r"^(?:please\s+)?remember\s+(?:that\s+)?",
    re.I,
)
TEMPORARY = re.compile(r"这次|这一轮|仅本次|暂时|\b(?:this time|for now|just this once)\b", re.I)
DURABLE = re.compile(
    r"以后|今后|从现在起|长期目标|每次都|\b(?:from now on|always|long.term goal)\b",
    re.I,
)
PERSONAL = re.compile(
    r"我(?:更|不再|不)?(?:喜欢|偏好|习惯|倾向)|我的(?:长期)?目标|"
    r"我(?:每天|每周|平时|通常|总是)|我(?:希望|计划|打算)(?:今年|未来|长期|成为|学会)|"
    r"\bI (?:prefer|usually|like|dislike|always)\b|\bmy long.term goal\b",
    re.I,
)
QUOTED = re.compile(
    r"[“”「」`]|他说|她说|例如|举例|引用|\b(?:he said|she said|for example)\b", re.I
)
STRUCTURE = re.compile(
    r"先.{0,3}结论|conclusion first|先.{0,4}(?:推导|解释).{0,12}(?:再|后).{0,4}结论|"
    r"explanation before",
    re.I,
)
PROJECT = re.compile(r"(?:这个|本|当前)(?:项目|工作区)|\bthis (?:project|workspace)\b", re.I)
THREAD = re.compile(r"这个会话|本会话|当前会话|\bthis (?:thread|conversation)\b", re.I)
NAMED_SCOPE = re.compile(
    r"在[^，。；]{1,48}(?:项目|工作区)(?:里|内|中|下|时|[，,])|"
    r"\b(?:for|in|within) (?:the )?[\w -]{1,48} (?:project|workspace)\b",
    re.I,
)
AMBIGUOUS_VALUES = {"都这样", "就这样", "照旧", "dothat", "sameasbefore"}
SENSITIVE = re.compile(
    r"密码|密钥|身份证|银行卡|\b(?:password|api[ _-]?key|secret key|private key)\b|"
    r"(?:sk-|ghp_|gho_|AKIA)[\w-]{12,}|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b\d{11,}\b",
    re.I,
)
EPISODE_TIME_ZH = re.compile(
    r"^(?:前天|昨天|今天|上周(?:[一二三四五六日天])?|上个月|这次|本次|"
    r"\d{4}(?:-\d{1,2}-\d{1,2}|年\d{1,2}月\d{1,2}日))[\s，,：:]*"
)
EPISODE_TIME_EN = re.compile(
    r"^(?:yesterday|today|last (?:week|month|Monday|Tuesday|Wednesday|Thursday|Friday|"
    r"Saturday|Sunday)|(?:on )?\d{4}-\d{2}-\d{2})[\s,]+",
    re.I,
)
RELATIVE_EPISODE_TIME = re.compile(
    r"前天|昨天|今天|上周|上个月|\b(?:today|yesterday|last (?:week|month|"
    r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b",
    re.I,
)
EPISODE_SCOPE = re.compile(
    r"^(?:在)?(?:这个|本|当前)(?:项目|工作区|会话)(?:里|内|中)?[\s，,：:]*|"
    r"^(?:in|for) this (?:project|workspace|thread|conversation)[\s,:]+",
    re.I,
)
EPISODE_SUBJECT = re.compile(r"^(?:我们?|团队)[\s，,]*")
EPISODE_ACTIONS = (
    r"完成|修复|解决|合并|发布|部署|迁移|发现|确认|切换|通过|验收|上线|交付|"
    r"参加|举办|签署|提交|收到|演示|重构"
)
EPISODE_ZH = re.compile(
    rf"^(?:(?:已(?:经)?|刚(?:刚)?|成功|终于|最终)\s*(?:{EPISODE_ACTIONS})(?:了)?|"
    rf"(?:{EPISODE_ACTIONS})了|(?:决定|敲定)(?:采用|使用|改用|将|用|了))"
    r"(?=\s*[^。！？；\s])"
)
EPISODE_OBJECT_ZH = re.compile(
    rf"^(?:已(?:经)?|刚(?:刚)?)?\s*(?:把|将)[^。；！？]{1, 64}?"
    rf"(?:(?:{EPISODE_ACTIONS})了|(?:切换|迁移|部署)到了?)"
)
EPISODE_EN = re.compile(
    r"^(?:I|we|our team)\s+(?:(?:just|already|successfully|finally|yesterday|today)\s+)*"
    r"(?:completed|finished|fixed|resolved|merged|released|deployed|migrated|discovered|"
    r"confirmed|switched|passed|shipped|attended|received|submitted|signed|presented|"
    r"launched|decided to)\s+\S",
    re.I,
)
HYPOTHETICAL = re.compile(r"如果|假设|\b(?:if|hypothetically|suppose)\b", re.I)


@dataclass(frozen=True)
class Claim:
    """One normalized assertion extracted from user text.
    / 从用户文本提取的一条规范化断言。"""

    key: str
    dimension: str
    value: str
    title: str
    scope: str
    scope_ref: str | None
    source: str


def reports_episode(text: str) -> bool:
    """Report whether text states a completed real event.

    Hypothetical or conditional statements never count; relative dates are stripped
    before matching so both Chinese and English reports are recognized.
    / 假设或条件陈述不计入；匹配前会剥离相对日期，因此中英文事件陈述都能识别。

    Args:
        text: Candidate sentence. / 候选句子。

    Returns:
        True when the sentence reports an actually completed event.
        / 句子报告了真实完成的事件时为 True。
    """
    if HYPOTHETICAL.search(text):
        return False
    report = EPISODE_SCOPE.sub("", text.strip(), count=1)
    if EPISODE_EN.match(EPISODE_TIME_EN.sub("", report, count=1)):
        return True
    report = EPISODE_SUBJECT.sub("", report, count=1)
    report = EPISODE_SCOPE.sub("", report, count=1)
    report = EPISODE_TIME_ZH.sub("", report, count=1)
    report = EPISODE_SUBJECT.sub("", report, count=1)
    report = EPISODE_SCOPE.sub("", report, count=1)
    return bool(EPISODE_ZH.match(report) or EPISODE_OBJECT_ZH.match(report))


def extract_claim(
    text: str,
    thread_id: str,
    project_id: str | None,
    recorded_at: str | None = None,
    run_id: str | None = None,
) -> Claim | None:
    """Extract one claim from a sentence, or reject the sentence.

    Rejects questions, sensitive content, quotations, pasted examples and purely
    temporary requests; infers global/project/thread scope from explicit wording and
    assigns a stable topic key.
    / 拒绝问句、敏感内容、引用、粘贴示例与纯临时请求；根据显式措辞推断全局/项目/线程
    作用域并生成稳定主题键。

    Args:
        text: Sentence-level user text. / 句子级用户文本。
        thread_id: Current thread, used for thread scope. / 当前线程，用于线程作用域。
        project_id: Current project, or None. / 当前项目或 None。
        recorded_at: Turn recording timestamp used for relative episode dates.
            / 用于相对事件日期的轮次记录时间。
        run_id: Run id used as a date fallback for replayed turns. / 回放轮次的日期兜底。

    Returns:
        The extracted claim, or None when the sentence is not learnable.
        / 提取出的断言；句子不可学习时为 None。
    """
    quote = text.strip()
    if (
        not quote
        or len(quote) > 800
        or "\n" in quote
        or quote.rstrip(" 。.!！").endswith(("?", "？"))
        or SENSITIVE.search(quote)
        or quote.startswith((">", '"', "“", "「", "`"))
    ):
        return None
    remember = REMEMBER.match(quote)
    durable = DURABLE.search(quote)
    value = quote[remember.end() :] if remember else quote
    episode = reports_episode(value)
    if episode and value.rstrip(" 。.!！").endswith(("吗", "么", "呢")):
        return None
    if (TEMPORARY.search(quote) and not episode) or not (
        episode or remember or durable or PERSONAL.search(quote)
    ):
        return None
    # English: Restrict extraction to direct statements, not pasted or quoted instructions.
    # 中文：仅提取直接陈述，而不是粘贴或引用的指令。
    if QUOTED.search(quote):
        return None
    if episode and THREAD.search(quote):
        scope, scope_ref = "thread", thread_id
    elif PROJECT.search(quote):
        if not project_id:
            return None
        scope, scope_ref = "project", project_id
    elif THREAD.search(quote):
        scope, scope_ref = "thread", thread_id
    elif NAMED_SCOPE.search(quote):
        return None
    elif episode and project_id:
        scope, scope_ref = "project", project_id
    else:
        scope, scope_ref = "global", None
    if episode:
        value = value.strip(" 。.!！")
        day = (
            (recorded_at[:10] if recorded_at else run_id)
            if RELATIVE_EPISODE_TIME.search(value)
            else None
        )
        key = "episode:" + _fingerprint(_normalized(value), day)[:20]
        return Claim(
            key,
            "episodic",
            value,
            value[:100],
            scope,
            scope_ref,
            "remember" if remember else "explicit",
        )
    value = re.sub(
        r"^(?:以后|今后|从现在起)[，,：:\s]*|^我(?:更)?(?:喜欢|偏好|习惯|倾向)\s*|"
        r"^(?:from now on[,:]?\s*|I (?:prefer|usually|like)\s+)",
        "",
        value.strip(),
        flags=re.I,
    ).strip(" 。.!！")
    if len(value) < 2 or _normalized(value) in AMBIGUOUS_VALUES:
        return None
    if STRUCTURE.search(value):
        key, dimension = "answer_structure", "preference"
        title = "方案与回答结构 / Answer structure"
    elif re.search(r"简洁|简短|详细|\b(?:concise|brief|detailed)\b", value, re.I) and re.search(
        r"回复|回答|解释|\b(?:answers?|replies|responses?|explanations?)\b", value, re.I
    ):
        key, dimension = "answer_detail", "preference"
        title = "回答详略 / Answer detail"
    else:
        dimension = (
            "goal"
            if re.search(r"目标|希望|计划|打算|\bgoal\b", quote, re.I)
            else "habit"
            if re.search(r"习惯|我(?:每天|每周|平时|通常|总是)|\b(?:usually|always)\b", quote, re.I)
            else "preference"
            if PERSONAL.search(quote) or durable
            else "fact"
        )
        key = dimension + ":" + _fingerprint(_normalized(value))[:20]
        title = value[:100]
    source = "remember" if remember else "explicit" if durable else "observed"
    return Claim(key, dimension, value, title, scope, scope_ref, source)


def _normalized(text: str) -> str:
    """Normalize text for identity comparisons.

    Args:
        text: Raw text. / 原始文本。

    Returns:
        Case-folded text with non-word characters removed. / 去除非单词字符并转小写的文本。
    """
    return re.sub(r"[\W_]+", "", text.casefold())


def _fingerprint(*values) -> str:
    """Hash JSON-encoded components into a stable hex digest.

    Args:
        *values: Components hashed after JSON encoding. / JSON 编码后参与哈希的各组成部分。

    Returns:
        Full SHA-256 hex digest of the encoded values. / 编码后值的完整 SHA-256 十六进制摘要。
    """
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def profile_key(dimension: str, value: str, title: str = "") -> str:
    """Derive a topic key that survives value and polarity changes.

    Known preference topics map to canonical keys; everything else hashes the topic
    text so the same subject keeps the same key.
    / 已知偏好主题映射到规范键；其余主题对主题文本取哈希，使同一主题保持同一键。

    Args:
        dimension: Claim dimension such as preference or fact. / 断言维度，如 preference 或 fact。
        value: Claimed value text. / 断言值文本。
        title: Optional human title used as a topic hint. / 可选的人类可读标题，用作主题提示。

    Returns:
        Stable topic key. / 稳定的主题键。
    """
    topics = (
        (
            "answer_structure",
            r"先.{0,3}结论|先.{0,4}(?:推导|解释)|conclusion first|explanation before",
        ),
        ("answer_detail", r"简洁|简短|精简|详细|长篇|啰嗦|\b(?:concise|brief|detailed|verbose)\b"),
        (
            "response_language",
            r"(?:用|使用|说|回答|回复).{0,8}(?:中文|英文|英语)|\b(?:answer|reply|respond).{0,12}(?:English|Chinese)\b",
        ),
        ("ui_theme", r"深色|浅色|暗色|亮色|dark mode|light mode"),
        ("ui_typography", r"衬线|无衬线|serif|sans.serif"),
        ("ui_accent", r"配色|强调色|铜色|accent colou?r"),
        ("programming_language", r"\b(?:Python|JavaScript|TypeScript|Rust|Golang)\b"),
    )
    if dimension == "preference":
        for key, pattern in topics:
            if re.search(pattern, value, re.I):
                return key
    topic = title or re.sub(
        r"^(?:我(?:更|不再|不|最)?(?:喜欢|偏好|习惯|倾向)|"
        r"I (?:do not |don't )?(?:prefer|like|dislike|usually))\s*",
        "",
        value,
        flags=re.I,
    )
    return dimension + ":" + _fingerprint(_normalized(topic))[:20]


def extract_claims(
    text: str, thread: str, project: str | None, recorded: str | None, run: str
) -> list[tuple[str, Claim]]:
    """Extract claims from prose lines, including list items.

    Fenced code, quote lines, headings and speculative fragments are ignored; a bare
    remember directive applies to following prose. At most eight claims are returned.
    / 忽略代码围栏、引用行、标题与推测片段；单独的“记住”指令作用于后续正文。最多返回
    八条断言。

    Args:
        text: Whole user message. / 完整用户消息。
        thread: Current thread id. / 当前线程 ID。
        project: Current project id, or None. / 当前项目 ID 或 None。
        recorded: Turn recording timestamp. / 轮次记录时间。
        run: Run id used as a date fallback. / 用作日期兜底的运行 ID。

    Returns:
        Pairs of evidence quote and extracted claim, deduplicated by topic and value.
        / 证据原话与提取断言的二元组，按主题与值去重。
    """
    prose = []
    fenced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced and not stripped.startswith((">", '"', "“", "「", "#")):
            prose.append(re.sub(r"^(?:[-*+] |\d+[.)、] )", "", stripped))
    clean = "\n".join(prose)
    scope_hint = "project" if PROJECT.search(clean) else "thread" if THREAD.search(clean) else None
    first_line = prose[0] if prose else ""
    directive = REMEMBER.match(first_line)
    remember_all = bool(directive and not first_line[directive.end() :].strip())
    result = []
    seen = set()
    for fragment in re.split(r"(?<=[。！？；])|(?<=[.!?;])\s+|\n+", clean)[:80]:
        fragment = fragment.strip()
        if not fragment or HYPOTHETICAL.search(fragment):
            continue
        candidate = extract_claim(fragment, thread, project, recorded, run)
        if candidate is None and remember_all and not REMEMBER.match(fragment):
            candidate = extract_claim("记住，" + fragment, thread, project, recorded, run)
        if candidate is None:
            continue
        scope, reference = candidate.scope, candidate.scope_ref
        if scope == "global" and scope_hint:
            if scope_hint == "project" and not project:
                continue
            scope, reference = scope_hint, project if scope_hint == "project" else thread
        key = (
            candidate.key
            if candidate.dimension == "episodic"
            else profile_key(candidate.dimension, candidate.value)
        )
        source = candidate.source
        if source == "observed" and PERSONAL.search(fragment):
            source = "explicit"
        candidate = Claim(
            key, candidate.dimension, candidate.value, candidate.title, scope, reference, source
        )
        identity = (key, _normalized(candidate.value), scope, reference)
        if identity not in seen:
            result.append((fragment, candidate))
            seen.add(identity)
        if len(result) == 8:
            break
    return result


class MemoryEvolution:
    """Apply extracted claims to the store and project confirmed facts into profiles.

    Args:
        store: Workspace store owning memory, claim and profile tables.
            / 持有记忆、断言与画像表的工作区存储。
        conversations: Conversation archive holding the learning queue.
            / 持有学习队列的会话归档。
    """

    def __init__(self, store: WorkspaceStore, conversations: SQLiteConversationStore):
        """Bind the workspace and conversation stores and create the write lock.

        Args:
            store: Workspace store used for memory and profile writes.
                / 用于记忆与画像写入的工作区存储。
            conversations: Conversation archive used for learning state.
                / 用于学习状态的会话归档。
        """
        self.store = store
        self.conversations = conversations
        self.db = store.connection
        self.lock = asyncio.Lock()
        self.planner = None

    async def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        """Run a query and return all rows as mappings.

        Args:
            sql: SQL text with placeholders. / 含占位符的 SQL 文本。
            args: Positional parameters. / 位置参数。

        Returns:
            All result rows as dictionaries. / 全部结果行（字典形式）。
        """
        async with self.db.execute(sql, args) as cursor:
            return [dict(row) async for row in cursor]

    @asynccontextmanager
    async def transaction(self):
        """Serialize memory writes inside an immediate transaction.

        Yields control while BEGIN IMMEDIATE holds the write lock; commits on success and
        rolls back on any exception.
        / 在 BEGIN IMMEDIATE 持有写锁期间交出控制权；成功时提交，任意异常时回滚。

        Yields:
            Control inside the transaction. / 事务内的控制权。
        """
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def run(self):
        """Reconcile startup metadata, then serve the learning queue.

        When a planner is configured it delegates to that worker; otherwise it processes
        rule-extracted claims one job at a time.
        / 配置了规划器时委托给该工作器；否则逐条处理规则提取的断言。
        """
        try:
            async with self.transaction():
                await self.refresh_relationships()
                for row in await self.rows("SELECT DISTINCT user_id FROM workspace_profiles"):
                    await self.store.prune_profile_history(row["user_id"])
        except Exception:
            logger.exception("Could not reconcile existing memory metadata")
        if self.planner is not None:
            await self.planner.run()
            return
        while True:
            try:
                for job in await self.conversations.learning_jobs():
                    try:
                        await self.process(job)
                    except Exception:
                        logger.exception("Memory extraction failed for a queued turn")
                        await self.conversations.defer_learning(*job[:4])
            except Exception:
                logger.exception("Could not read the memory learning queue")
            await asyncio.sleep(1)

    async def process(self, job: tuple):
        """Learn one queued turn or delegate it to the planner.

        Args:
            job: (row_id, user, thread, run, quote, project, recorded_at) tuple.
                / 任务元组。
        """
        if self.planner is not None:
            await self.planner.process_jobs([job])
            return
        row_id, user, thread, run, quote, project, recorded_at = job
        candidates = extract_claims(quote, thread, project, recorded_at, run)
        if candidates:
            async with self.transaction():
                if await self.conversations.personalization_enabled(
                    user
                ) and await self.conversations.learning_pending(user, thread, run):
                    for evidence, candidate in candidates:
                        await self.record(user, thread, run, evidence, candidate, recorded_at)
                    await self.rebuild(user)
                    await self.refresh_relationships(user)
        await self.conversations.finish_learning(row_id, user, thread, run)

    async def record(
        self,
        user: str,
        thread: str,
        run: str,
        quote: str,
        claim: Claim,
        recorded_at: str | None,
    ):
        """Record evidence for a claim and advance its lifecycle.

        Claims are inserted as observed; explicit or corroborated ones become pending, and
        remember directives confirm immediately when no conflict exists.
        / 断言先以 observed 写入；显式或有佐证的转为 pending；无明显冲突时“记住”指令会立即
        确认。

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Source thread id. / 来源线程 ID。
            run: Source run id. / 来源运行 ID。
            quote: Evidence quote from the source text. / 来源文本中的证据原话。
            claim: Extracted claim. / 提取出的断言。
            recorded_at: Recording timestamp of the turn. / 轮次的记录时间。
        """
        claim_id = _fingerprint(
            user, claim.scope, claim.scope_ref, claim.key, _normalized(claim.value)
        )
        await self.db.execute(
            """INSERT INTO memory_claims
               (id, user_id, scope, scope_ref, profile_key, dimension, value, title, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'observed') ON CONFLICT DO NOTHING""",
            (
                claim_id,
                user,
                claim.scope,
                claim.scope_ref,
                claim.key,
                claim.dimension,
                claim.value,
                claim.title,
            ),
        )
        row = (await self.rows("SELECT * FROM memory_claims WHERE id=?", (claim_id,)))[0]
        if row["status"] == "ignored":
            return
        if row["status"] == "superseded":
            if claim.source == "observed" or await self.rows(
                "SELECT 1 FROM memory_evidence WHERE claim_id=? AND thread_id=? AND run_id=?",
                (claim_id, thread, run),
            ):
                return
            await self.db.execute(
                "UPDATE memory_claims SET status='pending' WHERE id=?", (claim_id,)
            )
        await self.db.execute(
            """INSERT INTO memory_evidence (claim_id, thread_id, run_id, quote, source, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
            (claim_id, thread, run, quote, claim.source, recorded_at),
        )
        if row["status"] == "confirmed":
            return
        conflict = await self.conflicts(row)
        if claim.source == "remember" and row["status"] != "superseded" and not conflict:
            await self.confirm(row, claim.value, rebuild=False)
            return
        count = (
            await self.rows(
                "SELECT COUNT(DISTINCT thread_id) AS count FROM memory_evidence WHERE claim_id=?",
                (claim_id,),
            )
        )[0]["count"]
        if claim.source != "observed" or count >= 2:
            await self.db.execute(
                "UPDATE memory_claims SET status='pending' WHERE id=?", (claim_id,)
            )

    async def conflicts(self, claim: dict) -> list[dict]:
        """Find confirmed claims that conflict with the candidate's topic.

        Args:
            claim: Claim row being evaluated. / 正在评估的断言行。

        Returns:
            Confirmed claims sharing the topic key, directly or by derived topic.
            / 直接或经推导主题共享主题键的已确认断言。
        """
        candidates = await self.rows(
            """SELECT c.id, c.memory_id, c.profile_key, c.dimension, m.content AS value
               FROM memory_claims c JOIN workspace_memories m
               ON m.id=c.memory_id WHERE c.user_id=? AND c.scope=? AND c.scope_ref IS ?
               AND c.dimension=? AND c.id<>? AND c.status='confirmed' AND m.status='active'""",
            (
                claim["user_id"],
                claim["scope"],
                claim["scope_ref"],
                claim["dimension"],
                claim["id"],
            ),
        )
        key = claim["profile_key"]
        return [
            item
            for item in candidates
            if item["profile_key"] == key
            or (
                not key.startswith("manual:")
                and profile_key(item["dimension"], item["value"]) == key
            )
        ]

    async def confirm(self, claim: dict, value: str, *, rebuild: bool = True) -> str:
        """Confirm a claim and project its value into a memory.

        Conflicting confirmed claims are archived and superseded first; the memory is
        updated in place when it already exists, otherwise created with a mapped type.
        / 冲突的已确认断言会先归档并标记被取代；记忆已存在时原地更新，否则按映射类型新建。

        Args:
            claim: Claim row ready for confirmation. / 可确认的断言行。
            value: Final value to store. / 最终存储值。
            rebuild: Whether to rebuild the profile and relations afterwards.
                / 之后是否重建画像与关系。

        Returns:
            The confirmed memory id. / 已确认的记忆 ID。
        """
        user = claim["user_id"]
        for previous in await self.conflicts(claim):
            await self.store.update_memory(user, previous["memory_id"], {"status": "archived"})
            await self.db.execute(
                "UPDATE memory_claims SET status='superseded' WHERE id=?", (previous["id"],)
            )
        memory = None
        if claim["memory_id"]:
            memory = await self.store.update_memory(
                user, claim["memory_id"], {"content": value, "status": "active"}
            )
        if memory is None:
            memory_type = {
                "episodic": "episodic",
                "habit": "preference",
                "preference": "preference",
            }.get(claim["dimension"], "semantic")
            memory = await self.store.create_memory(
                user,
                {
                    "title": claim["title"],
                    "content": value,
                    "scope": claim["scope"],
                    "scope_ref": claim["scope_ref"],
                    "source": "conversation",
                    "type": memory_type,
                    "importance": claim.get("importance")
                    if claim.get("importance") is not None
                    else 0.7,
                },
            )
        await self.db.execute(
            """UPDATE memory_claims SET status='confirmed', memory_id=?, value=?,
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id=?""",
            (memory["id"], value, claim["id"]),
        )
        if rebuild:
            await self.rebuild(user)
            await self.refresh_relationships(user)
        return memory["id"]

    async def rebuild(self, user: str):
        """Project confirmed preference-like claims into a new profile version.

        A version is only written when the attribute list actually changes; the version
        counter guards against overflow and history is pruned afterwards.
        / 仅当属性列表实际变化时才写入新版本；版本计数器防止溢出，随后裁剪历史。

        Args:
            user: Owning user id. / 所属用户 ID。
        Raises:
            ValueError: When the profile version counter is exhausted.
                / 画像版本计数器耗尽时抛出。
        """
        attributes = await self.rows(
            """SELECT c.profile_key AS key, c.dimension, m.content AS value, c.scope, c.scope_ref,
                      c.memory_id, c.updated_at
               FROM memory_claims c JOIN workspace_memories m ON m.id=c.memory_id
               WHERE c.user_id=? AND c.status='confirmed' AND m.status='active'
                 AND c.dimension IN ('preference', 'habit', 'goal')
               ORDER BY c.scope, c.scope_ref, c.profile_key""",
            (user,),
        )
        previous = await self.store.get_profile(user)
        if attributes == (previous or {}).get("attributes", []):
            return
        version = (previous or {}).get("version", 0) + 1
        if version >= 2**63 - 1:
            raise ValueError("Profile version capacity reached")
        current_ids = {attribute["memory_id"] for attribute in attributes}
        removed = sum(
            attribute["memory_id"] not in current_ids
            for attribute in (previous or {}).get("attributes", [])
        )
        await self.db.execute(
            """INSERT INTO workspace_profiles
               (user_id, version, profile_json, change_summary, attributes_json, removed_attributes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user,
                version,
                json.dumps((previous or {}).get("preferences", {}), ensure_ascii=False),
                "Updated from confirmed memory",
                json.dumps(attributes, ensure_ascii=False),
                removed,
            ),
        )
        await self.store.prune_profile_history(user)

    async def refresh_relationships(self, user: str | None = None):
        """Recompute conversation-origin edges between memories.

        Edges are rebuilt from shared-thread evidence; obsolete conversation edges are
        removed while manual edges are preserved.
        / 依据共享线程证据重建边；过期的会话边被移除，手动边保留。

        Args:
            user: Optional user filter; None rebuilds all users.
                / 可选用户过滤；None 表示重建全部用户。
        """
        await self.db.execute(
            """DELETE FROM workspace_memory_links AS l WHERE origin='conversation' AND suppressed=0
               AND source_id IN (SELECT id FROM workspace_memories WHERE ? IS NULL OR user_id=?)
               AND NOT EXISTS (
                   SELECT 1 FROM memory_claims a JOIN memory_evidence e ON e.claim_id=a.id
                   JOIN memory_evidence f ON f.thread_id=e.thread_id
                   JOIN memory_claims b ON b.id=f.claim_id AND b.user_id=a.user_id
                   WHERE a.memory_id=l.source_id AND b.memory_id=l.target_id
                     AND a.status='confirmed' AND b.status='confirmed'
               )""",
            (user, user),
        )
        memories = await self.rows(
            """SELECT DISTINCT m.id,m.user_id FROM workspace_memories m
               JOIN memory_claims c ON c.memory_id=m.id
               WHERE m.status='active' AND c.status='confirmed' AND (? IS NULL OR m.user_id=?)""",
            (user, user),
        )
        for memory in memories:
            neighbors = await self.rows(
                """SELECT DISTINCT other.memory_id AS id FROM memory_claims own
                   JOIN memory_evidence e ON e.claim_id=own.id
                   JOIN memory_evidence related ON related.thread_id=e.thread_id
                   JOIN memory_claims other ON other.id=related.claim_id
                       AND other.user_id=own.user_id
                   JOIN workspace_memories m ON m.id=other.memory_id
                   WHERE own.memory_id=? AND other.memory_id<>? AND other.status='confirmed'
                     AND m.status='active' ORDER BY other.updated_at DESC LIMIT 5""",
                (memory["id"], memory["id"]),
            )
            for neighbor in neighbors:
                source, target = sorted((memory["id"], neighbor["id"]))
                await self.db.execute(
                    """INSERT INTO workspace_memory_links
                       (source_id,target_id,relation,relevance,origin)
                       VALUES (?,?,'shared_conversation',0.7,'conversation')
                       ON CONFLICT DO NOTHING""",
                    (source, target),
                )

    async def create_memory(
        self, user: str, values: dict, dimension: str | None = None, key: str | None = None
    ):
        """Create a memory manually and optionally link it to a claim.

        Args:
            user: Owning user id. / 所属用户 ID。
            values: Memory fields for the new row. / 新记忆行的字段。
            dimension: Optional claim dimension to create alongside the memory.
                / 可选，为记忆同时创建的断言维度。
            key: Optional explicit topic key for the claim. / 断言的可选显式主题键。

        Returns:
            The created memory snapshot. / 创建后的记忆快照。
        """
        async with self.transaction():
            values = {**values, "source": values.get("source") or "manual"}
            memory = await self.store.create_memory(user, values)
            dimension = dimension or ("preference" if memory["type"] == "preference" else None)
            if dimension:
                claim_id = "manual:" + _fingerprint(user, memory["id"])
                topic = key or profile_key(dimension, memory["content"], memory["title"])
                await self.db.execute(
                    """INSERT INTO memory_claims
                       (id,user_id,scope,scope_ref,profile_key,dimension,value,title,status,memory_id)
                       VALUES (?,?,?,?,?,?,?,?,'pending',?)""",
                    (
                        claim_id,
                        user,
                        memory["scope"],
                        memory["scope_ref"],
                        topic,
                        dimension,
                        memory["content"],
                        memory["title"],
                        memory["id"],
                    ),
                )
                claim = (await self.rows("SELECT * FROM memory_claims WHERE id=?", (claim_id,)))[0]
                await self.confirm(claim, memory["content"])
            return memory

    async def update_profile(self, user: str, expected: int, preferences: dict, summary: str):
        """Replace manual profile preferences under optimistic versioning.

        Args:
            user: Owning user id. / 所属用户 ID。
            expected: Version the caller read. / 调用方读到的版本。
            preferences: New manual preference mapping. / 新的手动偏好映射。
            summary: Change summary stored with the version. / 随版本保存的变更摘要。

        Returns:
            The new profile snapshot, or None when the version was stale.
            / 新的画像快照；版本过期时为 None。
        """
        async with self.transaction():
            saved = await self.store.save_profile(user, expected, preferences, summary)
            return await self.store.get_profile(user) if saved else None

    async def evidence(self, claim_id: str):
        """List the evidence rows backing one claim.

        Args:
            claim_id: Claim identifier. / 断言标识。

        Returns:
            Evidence rows in insertion order. / 按插入顺序排列的证据行。
        """
        return await self.rows(
            "SELECT thread_id, run_id, quote, source, recorded_at FROM memory_evidence "
            "WHERE claim_id=? ORDER BY rowid",
            (claim_id,),
        )

    async def suggestions(self, user: str):
        """List pending claims with their evidence for review.

        Args:
            user: Owning user id. / 所属用户 ID。

        Returns:
            Pending claims, newest first, each carrying its evidence list.
            / 按从新到旧排列且携带证据列表的待处理断言。
        """
        async with self.lock:
            claims = await self.rows(
                """SELECT id, value, title, dimension, profile_key, scope, scope_ref
                   FROM memory_claims
                   WHERE user_id=? AND status='pending' ORDER BY rowid DESC LIMIT 100""",
                (user,),
            )
            for claim in claims:
                claim["evidence"] = await self.evidence(claim["id"])
            return claims

    async def review(
        self, user: str, claim_id: str, action: str, value: str | None, key: str | None = None
    ):
        """Accept or ignore one pending claim.

        Ignoring records learning exclusions so the same turns are not re-learned; accepting
        confirms the claim, optionally rewrites its topic key and requires evidence.
        / 忽略会记录学习排除项以避免同一轮次被重新学习；接受会确认断言，可重写主题键，且
        必须存在证据。

        Args:
            user: Owning user id. / 所属用户 ID。
            claim_id: Claim identifier. / 断言标识。
            action: Either ignore or accept. / ignore 或 accept。
            value: Replacement value when accepting, or None. / 接受时的替换值或 None。
            key: Optional replacement topic key. / 可选替换主题键。

        Returns:
            Result status mapping, or None when the claim is missing or unsupported.
            / 结果状态映射；断言缺失或不可处理时为 None。
        """
        async with self.transaction():
            claims = await self.rows(
                "SELECT * FROM memory_claims WHERE user_id=? AND id=?", (user, claim_id)
            )
            if not claims:
                return None
            claim = claims[0]
            if claim["status"] != "pending":
                return {"status": claim["status"], "memory_id": claim["memory_id"]}
            if action == "ignore":
                await self.db.execute(
                    "INSERT OR IGNORE INTO memory_learning_exclusions "
                    "SELECT ?,thread_id,run_id FROM memory_evidence WHERE claim_id=?",
                    (user, claim_id),
                )
                await self.db.execute(
                    "UPDATE memory_claims SET status='ignored' WHERE id=?", (claim_id,)
                )
                return {"status": "ignored", "memory_id": None}
            if not await self.evidence(claim_id):
                return None
            if key is not None and key != claim["profile_key"]:
                await self.db.execute(
                    "UPDATE memory_claims SET profile_key=? WHERE id=?", (key, claim_id)
                )
                claim["profile_key"] = key
            return {
                "status": "confirmed",
                "memory_id": await self.confirm(claim, value or claim["value"]),
            }

    async def memory_evidence(self, user: str, memory_id: str):
        """List evidence for the claim attached to one memory.

        Args:
            user: Owning user id. / 所属用户 ID。
            memory_id: Memory identifier. / 记忆标识。

        Returns:
            Evidence rows, or an empty list when the memory has no claim.
            / 证据行；记忆没有断言时为空列表。
        """
        async with self.lock:
            claims = await self.rows(
                "SELECT id FROM memory_claims WHERE user_id=? AND memory_id=?", (user, memory_id)
            )
            return await self.evidence(claims[0]["id"]) if claims else []

    async def receipts(self, user: str, thread: str, run: str):
        """Return what one run changed in memory for the UI.

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Thread identifier. / 线程标识。
            run: Run identifier. / 运行标识。

        Returns:
            Confirmed memories plus ids of claims still pending review.
            / 已确认记忆与仍待审查断言 ID 的二元组。
        """
        async with self.lock:
            items = await self.rows(
                """SELECT DISTINCT m.id, m.title, m.scope, m.type FROM workspace_memories m
                   JOIN memory_claims c ON c.memory_id=m.id
                   JOIN memory_evidence e ON e.claim_id=c.id
                   WHERE c.user_id=? AND e.thread_id=? AND e.run_id=?
                     AND (e.source='remember' OR c.learned_by='llm')
                     AND c.status='confirmed' AND m.status='active'""",
                (user, thread, run),
            )
            suggestions = await self.rows(
                """SELECT c.id FROM memory_claims c JOIN memory_evidence e ON e.claim_id=c.id
                   WHERE c.user_id=? AND e.thread_id=? AND e.run_id=? AND c.status='pending'""",
                (user, thread, run),
            )
            return items, [item["id"] for item in suggestions]

    async def edit_memory(self, user: str, memory_id: str, values: dict):
        """Edit a memory and keep its claim, profile and exclusions consistent.

        Archiving excludes the source turns from future learning; reactivating confirms the
        attached claim; other edits rebuild the profile and relations.
        / 归档会把来源轮次排除出后续学习；重新激活会确认关联断言；其他编辑会重建画像与关系。

        Args:
            user: Owning user id. / 所属用户 ID。
            memory_id: Memory identifier. / 记忆标识。
            values: Fields to update. / 要更新的字段。

        Returns:
            The updated memory snapshot, or None when it does not exist.
            / 更新后的记忆快照；不存在时为 None。
        """
        async with self.transaction():
            result = await self.store.update_memory(user, memory_id, values)
            if result and values.get("status") == "archived":
                await self.db.execute(
                    "INSERT OR IGNORE INTO memory_learning_exclusions "
                    "SELECT ?,e.thread_id,e.run_id FROM memory_evidence e JOIN memory_claims c "
                    "ON c.id=e.claim_id WHERE c.user_id=? AND c.memory_id=?",
                    (user, user, memory_id),
                )
            claims = await self.rows(
                "SELECT * FROM memory_claims WHERE user_id=? AND memory_id=?", (user, memory_id)
            )
            if result and values.get("status") == "active" and claims:
                await self.confirm(claims[0], result["content"])
            else:
                await self.rebuild(user)
                await self.refresh_relationships(user)
            return result

    async def delete_memory(self, user: str, memory_id: str):
        """Delete a memory, its evidence and its profile traces.

        Source turns are excluded from future learning and redacted from stored snapshots
        and profile history.
        / 来源轮次会被排除出后续学习，并从已存快照与画像历史中擦除。

        Args:
            user: Owning user id. / 所属用户 ID。
            memory_id: Memory identifier. / 记忆标识。

        Returns:
            True when a memory row was removed. / 删除了记忆行时为 True。
        """
        async with self.transaction():
            await self.db.execute(
                """INSERT OR IGNORE INTO memory_learning_exclusions
                   SELECT ?,e.thread_id,e.run_id FROM memory_evidence e JOIN memory_claims c
                   ON c.id=e.claim_id WHERE c.user_id=? AND c.memory_id=?""",
                (user, user, memory_id),
            )
            await self.db.execute(
                """DELETE FROM memory_evidence WHERE claim_id IN (
                   SELECT id FROM memory_claims WHERE user_id=? AND memory_id=?)""",
                (user, memory_id),
            )
            await self.db.execute(
                """UPDATE memory_claims SET status='ignored', value='', title=''
                   WHERE user_id=? AND memory_id=?""",
                (user, memory_id),
            )
            removed = await self.store.delete_memory(user, memory_id)
            await self.rebuild(user)
            if removed:
                await self.redact_profile_history(user, memory_id)
            return removed

    async def redact_profile_history(self, user: str, memory_id: str):
        """Erase one memory id from stored snapshots and profile history.

        Args:
            user: Owning user id. / 所属用户 ID。
            memory_id: Memory id to redact. / 要擦除的记忆 ID。
        """
        from app.infrastructure.prompt_cache import ProfileSnapshots

        await ProfileSnapshots(self.store).redact_memory(user, memory_id)
        await self.db.execute(
            """UPDATE workspace_profiles SET redacted_attributes=redacted_attributes+(
                   SELECT count(*) FROM json_each(attributes_json) item
                   WHERE json_extract(item.value, '$.memory_id') = ?
               ), attributes_json=(
                   SELECT json_group_array(json(item.value)) FROM json_each(attributes_json) item
                   WHERE json_extract(item.value, '$.memory_id') <> ?
               ) WHERE user_id=? AND EXISTS (
                   SELECT 1 FROM json_each(attributes_json) item
                   WHERE json_extract(item.value, '$.memory_id') = ?
               )""",
            (memory_id, memory_id, user, memory_id),
        )

    async def forget_thread(self, user: str, thread: str, delete_source: Callable[[], Awaitable]):
        """Forget one thread's learning and repair affected claims.

        Evidence from the thread is deleted; claims left without evidence lose their
        memories; pending claims below the corroboration threshold revert to observed;
        profiles are rebuilt and redacted, then the caller's delete callback runs.
        / 删除该线程的证据；失去全部证据的断言连同记忆一起删除；佐证不足的待处理断言回退为
        observed；重建并擦除画像，最后执行调用方的删除回调。

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Thread being deleted or cleared. / 被删除或清空的线程。
            delete_source: Awaitable deleting the thread history after learning state.
                / 在学习状态修复后删除线程历史的可等待对象。
        """
        async with self.transaction():
            await self.conversations.discard_thread_learning(user, thread)
            await self.db.execute(
                "DELETE FROM memory_learning_turns WHERE user_id=? AND thread_id=?", (user, thread)
            )
            await self.db.execute(
                "DELETE FROM memory_learning_exclusions WHERE user_id=? AND thread_id=?",
                (user, thread),
            )
            await self.db.execute(
                """DELETE FROM memory_evidence WHERE thread_id=? AND claim_id IN (
                   SELECT id FROM memory_claims WHERE user_id=?)""",
                (thread, user),
            )
            unsupported = await self.rows(
                """SELECT id, memory_id FROM memory_claims c WHERE c.user_id=?
                   AND c.status<>'ignored' AND c.id NOT LIKE 'manual:%' AND NOT EXISTS (
                       SELECT 1 FROM memory_evidence e WHERE e.claim_id=c.id)""",
                (user,),
            )
            for claim in unsupported:
                if claim["memory_id"]:
                    await self.store.delete_memory(user, claim["memory_id"])
                await self.db.execute("DELETE FROM memory_claims WHERE id=?", (claim["id"],))
            await self.db.execute(
                """UPDATE memory_claims AS c SET status='observed'
                   WHERE c.user_id=? AND c.status='pending' AND (
                       SELECT count(DISTINCT e.thread_id) FROM memory_evidence e
                       WHERE e.claim_id=c.id
                   ) < 2 AND NOT EXISTS (
                       SELECT 1 FROM memory_evidence e WHERE e.claim_id=c.id
                       AND e.source IN ('explicit', 'remember')
                   )""",
                (user,),
            )
            await self.rebuild(user)
            for claim in unsupported:
                if claim["memory_id"]:
                    await self.redact_profile_history(user, claim["memory_id"])
            await self.refresh_relationships(user)
        await delete_source()
