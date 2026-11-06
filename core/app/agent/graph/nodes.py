"""在线 Graph 节点，仅编排领域服务。 / Online nodes orchestrating read-only domain services."""

import asyncio
from copy import deepcopy
from time import perf_counter
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
)
from langchain_core.runnables import Runnable, RunnableConfig

from app.agent.graph.services import AgentServices, ContextBuildResult, HistorySummaryResult
from app.agent.graph.state import AgentState
from app.agent.prompting.history import clip, extractive_summary
from app.agent.prompting.plan import compose, reference
from app.agent.prompting.telemetry import cache_write_tokens
from app.config import AgentSettings, PromptSettings
from app.tools.read_attachment import attachment_references


class AgentNodes:
    """绑定一次依赖，供节点重复调用。 / Bind dependencies once for all node invocations.

    Args:
        services: 只读接口与模型入口。 / Read-only ports and the model provider.
        settings: 最大读取耗时、候选数和上下文预算。 / Read timeout, candidate and context budgets.
        model: 已按需绑定工具的模型 Runnable。 / Model runnable with optional tools bound.
        system_prompt: 可信基础指令，不包含用户历史。 / Trusted instructions without user history.
        tools_enabled: 是否显式注册了工具。 / Whether tools were explicitly registered.
    """

    def __init__(
        self,
        services: AgentServices,
        settings: AgentSettings,
        model: Runnable[LanguageModelInput, AIMessage],
        system_prompt: str,
        tools_enabled: bool = False,
        prompt_settings: PromptSettings | None = None,
        toolset_hash: str = "",
        cache_protocol: str = "openai",
    ) -> None:
        self.services = services
        self.settings = settings
        self.model = model
        self.system_prompt = system_prompt
        self.tools_enabled = tools_enabled
        self.prompt_settings = prompt_settings or PromptSettings()
        self.legacy_history = self.prompt_settings.layout == "legacy" or (
            prompt_settings is None and settings.history_max_turns != 20
        )
        self.toolset_hash, self.cache_protocol = toolset_hash, cache_protocol

    async def prepare_run(self, state: AgentState) -> dict[str, Any]:
        """校验并准备新一轮执行。 / Validate and prepare a new run.

        Args:
            state: 新请求合并后的工作状态。 / Working state after merging the new request.

        中文：整轮保留 Human/AI/Tool 消息，旧快照本身不删除；恢复时从待执行节点继续。
        English: Retain complete human/AI/tool turns without deleting older snapshots.
        """
        for name in ("user_id", "thread_id", "run_id", "user_input"):
            if not isinstance(state.get(name), str) or not state[name].strip():
                raise ValueError(f"{name} must be a non-empty string")
        messages = list(state.get("messages", []))
        if (
            not messages
            or not isinstance(messages[-1], HumanMessage)
            or messages[-1].content != state["user_input"]
            or messages[-1].id != f"{state['run_id']}:user"
        ):
            raise ValueError("A new run must end with its current user message")
        turns = [
            index for index, message in enumerate(messages) if isinstance(message, HumanMessage)
        ]
        update: dict[str, Any] = {
            "personalization_enabled": self.settings.personalization_enabled
            and state.get("personalization_enabled", True),
            "user_profile": None,
            "profile_version": None,
            "profile_snapshot": None,
            "relevant_memories": [],
            "personalization_context": "",
            "used_memory_ids": [],
            "context_token_estimate": 0,
            "response": "",
            "error": None,
            "warnings": [],
            "history_compression_pending": len(turns) > self.prompt_settings.history_soft_limit,
        }
        if self.legacy_history and len(turns) > self.settings.history_max_turns:
            start = turns[-self.settings.history_max_turns]
            update["messages"] = [RemoveMessage(id=message.id) for message in messages[:start]]
            update["history_compression_pending"] = False
        elif not self.legacy_history and len(turns) > self.prompt_settings.history_hard_limit:
            batch = min(self.prompt_settings.history_compression_batch, len(turns) - 1)
            start = turns[batch]
            previous = state.get("history_summary")
            text = extractive_summary(messages[:start], previous)
            method = "extractive"
            if self.services.history_summarizer:
                try:
                    started = perf_counter()
                    async with asyncio.timeout(30):
                        generated = await self.services.history_summarizer(
                            messages[:start], previous
                        )
                    if isinstance(generated, HistorySummaryResult):
                        usage = generated.usage
                        if usage and usage["total_tokens"] > 0:
                            update["model_stats"] = [
                                *state.get("model_stats", []),
                                {
                                    "purpose": "history_summary",
                                    "input_tokens": usage["input_tokens"],
                                    "output_tokens": usage["output_tokens"],
                                    "total_tokens": usage["total_tokens"],
                                    "cached_tokens": (usage.get("input_token_details") or {}).get(
                                        "cache_read"
                                    ),
                                    "cache_write_tokens": cache_write_tokens(usage),
                                    "seconds": perf_counter() - started,
                                },
                            ]
                        generated = generated.text
                    if not generated.strip():
                        raise ValueError("Summary model returned empty text")
                    text, method = clip(generated, 10000), "model"
                except Exception:
                    update["warnings"].append("HISTORY_SUMMARY_FALLBACK")
            update["history_summary"] = {
                "version": (previous or {}).get("version", 0) + 1,
                "from_turn": 1,
                "through_turn": (previous or {}).get("through_turn", 0) + batch,
                "method": method,
                "text": text,
            }
            update["history_compression_pending"] = False
            update["messages"] = [RemoveMessage(id=message.id) for message in messages[:start]]
        return update

    async def load_profile(self, state: AgentState) -> dict[str, Any]:
        """state：本轮可信状态，仅加载画像。 / Current trusted state; only load the profile."""
        update: dict[str, Any] = {"user_profile": None, "profile_version": None}
        if not state["personalization_enabled"] or self.services.profile_reader is None:
            return update
        try:
            async with asyncio.timeout(self.settings.context_read_timeout_seconds):
                profile = await self.services.profile_reader.get_profile(state["user_id"])
            if profile is not None:
                version = profile.get("version")
                if profile.get("user_id") != state["user_id"]:
                    raise ValueError("Profile ownership mismatch")
                if type(version) is not int or version < 1:
                    raise ValueError("Profile version must be a positive integer")
                profile = deepcopy(profile)
                profile["active_thread_id"] = state["thread_id"]
                profile["attributes"] = [
                    attribute
                    for attribute in profile.get("attributes", [])
                    if isinstance(attribute, dict)
                    and isinstance(attribute.get("memory_id"), str)
                    and (
                        (attribute.get("scope") == "global" and not attribute.get("scope_ref"))
                        or (
                            attribute.get("scope") == "thread"
                            and attribute.get("scope_ref") == state["thread_id"]
                        )
                        or (
                            attribute.get("scope") == "project"
                            and state.get("project_id")
                            and attribute.get("scope_ref") == state["project_id"]
                        )
                    )
                ]
                update.update(user_profile=profile, profile_version=version)
        except TimeoutError:
            update["warnings"] = [*state.get("warnings", []), "PROFILE_READ_TIMEOUT"]
        except Exception:
            # 中文：不把服务异常正文注入模型，避免泄露连接配置等内部信息。
            # English: Never inject raw service exceptions or connection details into model input.
            update["warnings"] = [*state.get("warnings", []), "PROFILE_READ_FAILED"]
        return update

    async def retrieve_memories(self, state: AgentState) -> dict[str, Any]:
        """state：携带用户、线程与项目边界。 / State supplying user/thread/project boundaries."""
        if not state["personalization_enabled"] or self.services.memory_reader is None:
            return {"relevant_memories": []}
        warnings = list(state.get("warnings", []))
        try:
            async with asyncio.timeout(self.settings.context_read_timeout_seconds):
                candidates = await self.services.memory_reader.retrieve(
                    user_id=state["user_id"],
                    query=state["user_input"],
                    thread_id=state["thread_id"],
                    project_id=state.get("project_id"),
                    limit=self.settings.memory_top_k,
                )
            # 中文：在返回边界直接校验，不建立额外召回或存储辅助层。
            # English: Validate the boundary directly, without another retrieval/storage layer.
            memories = []
            seen = set()
            rejected = False
            for memory in candidates:
                identifier = memory.get("id")
                scope = memory.get("scope")
                reference = memory.get("scope_ref")
                allowed_scope = (
                    (scope == "global" and reference in (None, ""))
                    or (scope == "thread" and reference == state["thread_id"])
                    or (
                        scope == "project"
                        and state.get("project_id") is not None
                        and reference == state["project_id"]
                    )
                )
                if (
                    not isinstance(identifier, str)
                    or not identifier.strip()
                    or memory.get("user_id") != state["user_id"]
                    or memory.get("status") != "active"
                    or not allowed_scope
                ):
                    rejected = True
                    continue
                if identifier not in seen:
                    memories.append(deepcopy(memory))
                    seen.add(identifier)
                if len(memories) >= self.settings.memory_top_k:
                    break
            if rejected:
                warnings.append("MEMORY_RESULTS_REJECTED")
            return {"relevant_memories": memories, "warnings": warnings}
        except TimeoutError:
            warnings.append("MEMORY_READ_TIMEOUT")
        except Exception:
            warnings.append("MEMORY_READ_FAILED")
        return {"relevant_memories": [], "warnings": warnings}

    async def build_personalized_context(self, state: AgentState) -> dict[str, Any]:
        """state：只使用本轮读取快照。 / State containing only this run's loaded reference data."""
        empty: dict[str, Any] = {
            "personalization_context": "",
            "used_memory_ids": [],
            "context_token_estimate": 0,
            "profile_snapshot": None,
        }
        if not state["personalization_enabled"]:
            return empty
        if not state.get("user_profile") and not state.get("relevant_memories"):
            return empty
        warnings = list(state.get("warnings", []))
        if self.services.context_builder is None:
            return {**empty, "warnings": [*warnings, "CONTEXT_BUILDER_NOT_CONFIGURED"]}
        try:
            async with asyncio.timeout(self.settings.context_read_timeout_seconds):
                if (
                    self.services.profile_snapshot_reader
                    and self.prompt_settings.layout == "layered"
                ):
                    return await self._layered_context(state)
                result = await self.services.context_builder.build(
                    user_input=state["user_input"],
                    user_profile=deepcopy(state.get("user_profile")),
                    relevant_memories=deepcopy(state.get("relevant_memories", [])),
                    project_id=state.get("project_id"),
                    token_budget=self.settings.memory_context_token_budget,
                )
            result = ContextBuildResult.model_validate(result)
            available = {memory["id"] for memory in state.get("relevant_memories", [])}
            available.update(
                attribute["memory_id"]
                for attribute in (state.get("user_profile") or {}).get("attributes", [])
            )
            if result.token_estimate > self.settings.memory_context_token_budget:
                raise ValueError("Context token budget exceeded")
            if not set(result.memory_ids).issubset(available):
                raise ValueError("Context references unknown memory IDs")
            if bool(result.text.strip()) != (result.token_estimate > 0):
                raise ValueError("Context text and token estimate disagree")
            if result.memory_ids and not result.text.strip():
                raise ValueError("Empty context cannot use memories")
            return {
                "personalization_context": result.text,
                "used_memory_ids": list(dict.fromkeys(result.memory_ids)),
                "context_token_estimate": result.token_estimate,
            }
        except TimeoutError:
            warnings.append("CONTEXT_BUILD_TIMEOUT")
        except Exception:
            warnings.append("CONTEXT_BUILD_FAILED")
        return {**empty, "warnings": warnings}

    async def _layered_context(self, state: AgentState) -> dict[str, Any]:
        """Freeze only revocable global profile data; fresh scope-specific data stays in L3."""
        saved = await self.services.profile_snapshot_reader.read(
            state["user_id"], state["thread_id"], state.get("user_profile")
        )
        frozen = saved["profile"]
        budget = self.settings.memory_context_token_budget
        prefix = ContextBuildResult.model_validate(
            await self.services.context_builder.build(
                user_input="",
                user_profile=frozen,
                relevant_memories=[],
                project_id=None,
                token_budget=budget // 2,
            )
        )
        profile = deepcopy(state.get("user_profile"))
        if profile:
            unchanged_preferences = frozen["preferences"] if "Preferences: " in prefix.text else {}
            profile["preferences"] = {
                key: value
                for key, value in profile["preferences"].items()
                if unchanged_preferences.get(key) != value or key not in unchanged_preferences
            }
            frozen_attributes = {item["memory_id"]: item for item in frozen.get("attributes", [])}
            profile["attributes"] = [
                item
                for item in profile.get("attributes", [])
                if item["memory_id"] not in prefix.memory_ids
                or any(
                    item.get(key) != frozen_attributes[item["memory_id"]].get(key)
                    for key in ("key", "value", "dimension")
                )
            ]
        candidates = [
            item
            for item in state.get("relevant_memories", [])
            if item["id"] not in prefix.memory_ids
        ]
        tail = ContextBuildResult.model_validate(
            await self.services.context_builder.build(
                user_input=state["user_input"],
                user_profile=profile,
                relevant_memories=candidates,
                project_id=state.get("project_id"),
                token_budget=budget - prefix.token_estimate,
            )
        )
        if prefix.token_estimate + tail.token_estimate > budget:
            raise ValueError("Layered context exceeds budget")
        used = list(dict.fromkeys([*prefix.memory_ids, *tail.memory_ids]))
        allowed = {item["memory_id"] for item in frozen.get("attributes", [])}
        allowed.update(item["id"] for item in state.get("relevant_memories", []))
        allowed.update(
            item["memory_id"] for item in (state.get("user_profile") or {}).get("attributes", [])
        )
        if not set(used).issubset(allowed):
            raise ValueError("Layered context references unknown memories")
        snapshot = (
            {
                "reference_data": prefix.text,
                "memory_ids": list(prefix.memory_ids),
                "version": saved["version"],
                "revision": saved["revision"],
            }
            if prefix.text
            else None
        )
        return {
            "profile_snapshot": snapshot,
            "personalization_context": tail.text,
            "used_memory_ids": used,
            "context_token_estimate": prefix.token_estimate + tail.token_estimate,
        }

    async def agent(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """调用模型，失败直接传播。 / Invoke the model and propagate failures.

        Args:
            state: 本轮消息与受限参考文本。 / Working messages and bounded reference text.
            config: 传递回调与流式配置。 / Propagated callbacks and streaming configuration.
        """
        reference_update = {}
        if state["personalization_enabled"] and self.services.personalization_gate:
            try:
                async with asyncio.timeout(self.settings.context_read_timeout_seconds):
                    allowed = await self.services.personalization_gate(state["user_id"])
            except Exception:
                allowed = False
            if not allowed:
                reference_update = {
                    "personalization_enabled": False,
                    "user_profile": None,
                    "profile_version": None,
                    "relevant_memories": [],
                    "used_memory_ids": [],
                    "personalization_context": "",
                    "context_token_estimate": 0,
                    "profile_snapshot": None,
                }
                state = {**state, **reference_update}
        # Revalidate revocable memory data on every call, including a resumed tool round.
        if state["personalization_enabled"] and self.services.profile_snapshot_reader:
            refreshed = await self.load_profile(state)
            state = {**state, **refreshed}
            refreshed.update(await self.retrieve_memories(state))
            state = {**state, **refreshed}
            refreshed.update(await self.build_personalized_context(state))
            reference_update.update(refreshed)
            state = {**state, **refreshed}
        messages: list[AnyMessage] = []
        if self.services.skill_reader:
            skills = self.services.skill_reader()
            if skills:
                messages.append(reference("skill_reference", {"skills": skills}))
        if state.get("attachment_ids"):
            if self.services.attachment_reader is None:
                raise ValueError("Attachment reader is unavailable")
            attachments = await self.services.attachment_reader.read_attachments(
                state["user_id"], state["thread_id"], state["attachment_ids"]
            )
            messages.append(reference("attachment_reference", attachment_references(attachments)))
        if state["personalization_enabled"] and state.get("personalization_context"):
            # 中文：参考数据独立于系统指令，并且不写回工作消息或检查点历史。
            # English: Keep reference data separate from system instructions and stored messages.
            messages.insert(
                0,
                reference(
                    "personalization_reference",
                    {
                        "reference_data": state["personalization_context"],
                        "profile_version": state.get("profile_version"),
                        "memory_ids": [
                            identifier
                            for identifier in state.get("used_memory_ids", [])
                            if identifier
                            not in (state.get("profile_snapshot") or {}).get("memory_ids", [])
                        ],
                    },
                ),
            )
        plan = compose(
            system=self.system_prompt,
            settings=self.prompt_settings,
            messages=list(state["messages"]),
            run_id=state["run_id"],
            references=messages,
            snapshot=state.get("profile_snapshot"),
            summary=state.get("history_summary"),
            toolset_hash=self.toolset_hash,
        )
        started = perf_counter()
        response = await self.model.ainvoke(
            plan.for_provider(self.cache_protocol, self.prompt_settings.cache_breakpoints),
            config=config,
        )
        elapsed = perf_counter() - started
        if not isinstance(response, AIMessage) or response.invalid_tool_calls:
            raise ValueError("Model returned an invalid assistant response")
        if (response.tool_calls and not self.tools_enabled) or (
            not response.tool_calls and not response.text.strip()
        ):
            # 中文：坏响应不写入检查点，恢复时可重新调用模型，而非卡在最终整理节点。
            # English: Do not checkpoint bad responses; recovery can retry the model node.
            raise ValueError("Model did not produce a final text answer")
        update: dict[str, Any] = {**reference_update, "messages": [response]}
        usage = response.usage_metadata
        if self.services.prompt_observer:
            selection = state.get("model_selection") or {}
            namespace = f"{selection.get('id')}:{selection.get('effort')}:{self.toolset_hash}"
            self.services.prompt_observer(
                state["user_id"],
                state["thread_id"],
                state["run_id"],
                namespace,
                plan.metadata,
                usage,
            )
        if usage and usage["total_tokens"] > 0:
            update["model_stats"] = [
                *state.get("model_stats", []),
                {
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "total_tokens": usage["total_tokens"],
                    "cached_tokens": (usage.get("input_token_details") or {}).get("cache_read"),
                    "cache_write_tokens": cache_write_tokens(usage),
                    "seconds": elapsed,
                    **plan.metadata,
                },
            ]
        return update

    async def finalize_response(self, state: AgentState) -> dict[str, Any]:
        """state：必须以有效最终 AI 消息结尾。 / State must end with a valid final AI message."""
        message = state["messages"][-1]
        if not isinstance(message, AIMessage) or message.tool_calls or not message.text.strip():
            raise ValueError("Model did not produce a final text answer")
        return {"response": message.text, "error": None}
