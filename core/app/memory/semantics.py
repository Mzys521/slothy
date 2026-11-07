"""V3 semantic identities, independent strength and bounded identity intent.
/ V3 语义标识、独立强度及有界身份意图。"""

import hashlib
import json
import re
import unicodedata
from math import isfinite

SCORING_VERSION = 3
PROFILE_DIMENSIONS = {"identity", "skill", "preference", "habit", "goal"}
IDENTITY_KEYS = {
    "user.name": "identity.name",
    "name": "identity.name",
    "full_name": "identity.name",
    "identity.full_name": "identity.name",
    "user.full_name": "identity.name",
    "user.nickname": "identity.preferred_name",
    "identity.nickname": "identity.preferred_name",
    "nickname": "identity.preferred_name",
    "preferred_name": "identity.preferred_name",
}
LEVELS = {
    "新手": "beginner",
    "初学者": "beginner",
    "小白": "beginner",
    "入门": "beginner",
    "刚入门": "beginner",
    "novice": "beginner",
    "beginner": "beginner",
    "中级": "intermediate",
    "intermediate": "intermediate",
    "熟练": "advanced",
    "高级": "advanced",
    "advanced": "advanced",
    "精通": "expert",
    "专家": "expert",
    "expert": "expert",
}


def normalized(value: str) -> str:
    """Normalize aliases without conflating concept types. / 规范化同类概念别名。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip().casefold())


def mentions(text: str, term: str) -> bool:
    """Typed lexical evidence respects Latin boundaries, including C versus C++.
    / 拉丁概念按完整词边界匹配，避免 Java/JavaScript 或 C/C++ 混淆。"""
    text, term = normalized(text), normalized(term)
    if not term:
        return False
    if re.search(r"[a-z0-9]", term):
        return bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_+#])", text))
    return term in text


def identifier(*parts) -> str:
    """Stable private ID; callers include owner and semantic type. /
    含所有者和语义类型的稳定 ID。"""
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def frequency_strength(frequency: int, c: float = 4.0) -> float:
    """Independent saturation, never collection-normalized or time-decayed.
    / 独立饱和，无横向或时间耦合。"""
    if isinstance(frequency, bool) or not isinstance(frequency, int):
        raise TypeError("frequency must be an integer")
    if frequency < 0:
        raise ValueError("frequency must be >= 0")
    if isinstance(c, bool) or not isfinite(c) or c <= 0:
        raise ValueError("c must be finite and positive")
    return frequency / (frequency + c)


def canonical_key(key: str) -> str:
    """Canonical identity aliases share one fact slot. / 身份别名统一到同一事实槽位。"""
    return IDENTITY_KEYS.get(key, key)


def identity_intent(query: str) -> str | None:
    """Match direct self-identity questions only, not quoted/composite
    tasks. / 仅识别直接本人身份问答。"""
    text = query.strip().casefold().rstrip("？?！!。. ")
    text = re.sub(r"^(?:请问|请)[，,\s]*", "", text)
    text = re.sub(r"^(?:你还|你|do you still |do you )", "", text)
    patterns = {
        "name": r"我叫(?:什么|啥)(?:名字)?|我的(?:名字|姓名)是(?:什么|啥)|"
        r"(?:还)?记得我的(?:名字|姓名)(?:吗|么)?|what(?:'s| is) my (?:name|full name)",
        "preferred_name": r"你?(?:应该)?(?:怎么|如何)称呼我|我的昵称(?:是(?:什么|啥))?|"
        r"what(?:'s| is) my nickname|what (?:should|do) you call me",
        "identity": r"我是谁|(?:还)?记得我(?:吗|么)?|who am i|remember me",
    }
    for intent, pattern in patterns.items():
        if re.fullmatch(pattern, text):
            return intent
    return None


def identity_supported(value: str, quotes: str) -> bool:
    """Reject third-party/fictional names even if a model proposes an exact quote.
    / 即使模型引用准确，也不能把第三方或虚构姓名变成本人事实。"""
    name = re.escape(value.strip())
    direct = re.compile(
        r"(?:我叫|我是|我的(?:名字|姓名)(?:是|叫)|(?:请)?(?:叫|称呼)我)\s*[：:是]?\s*[‘“\"「]?"
        + name
        + r"|\b(?:my name is|i am|i'm|call me)\s+[\"']?"
        + name,
        re.I,
    )
    foreign = re.compile(
        r"他说|她说|同事|同学|朋友|角色扮演|假设|虚构|例如|举例|如果|"
        r"\b(?:he said|she said|colleague|fictional|roleplay|suppose|example|if)\b",
        re.I,
    )
    return any(
        direct.search(part) and not foreign.search(part) for part in re.split(r"[。；;\n]", quotes)
    )


def personal_skill_supported(quotes: str) -> bool:
    """A personal skill needs a self statement, not a friend's trait or task mention.
    / 技能画像需要本人陈述，朋友的属性和普通任务提及不算来源。"""
    return bool(
        re.search(
            r"我(?:最近|现在|还|仍|刚|也|正在|在|已经|目前|还是|是|对|学|会|掌握|熟悉|使用|练习|开始|不)|"
            r"我的(?:技能|水平|熟练度|能力)|\bI\b|\bmy\s+(?:skill|level|proficiency|knowledge|experience)\b",
            quotes,
            re.I,
        )
    )


def scope_allowed(scope: str, reference: str | None, thread: str, project: str | None) -> bool:
    """One scope policy shared by retrieval and graph. / 检索与图谱共用作用域策略。"""
    return (
        (scope == "global" and not reference)
        or (scope == "thread" and reference == thread)
        or (scope == "project" and project is not None and reference == project)
    )
