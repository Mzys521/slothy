"""Read native checkpoint state, including the first input checkpoint.
/ 读取原生检查点状态，包括尚未执行 START 的初始输入检查点。"""

from langchain_core.runnables import RunnableConfig
from langgraph.graph import START
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot


async def checkpoint_state(
    graph: CompiledStateGraph, config: RunnableConfig
) -> tuple[StateSnapshot, dict]:
    """Return the latest snapshot and its saved request without starting execution.
    / 返回最新快照与保存的原始请求，不启动执行或重建输入。"""
    snapshot = await graph.aget_state(config)
    values = snapshot.values
    if START in snapshot.next:
        saved = await graph.checkpointer.aget_tuple(snapshot.config)
        seed = saved.checkpoint["channel_values"].get(START) if saved else None
        if isinstance(seed, dict):
            values = seed
    return snapshot, values
