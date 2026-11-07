"""Run from core/: python -m examples.chat '你好' [--stream]."""

import argparse
import asyncio

from app.infrastructure.llm import create_model_provider


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the example chat client. / 解析示例聊天客户端的命令行参数。

    Returns:
        Parsed namespace with message and stream flag. / 含消息与 stream 标志的解析结果。
    """
    parser = argparse.ArgumentParser(description="Call the configured model provider")
    parser.add_argument("message", nargs="?", default="请用一句话介绍你自己。")
    parser.add_argument("--stream", action="store_true")
    return parser.parse_args()


async def main(message: str, stream: bool) -> None:
    """Send one message to the configured model and print the reply.
    / 向已配置模型发送一条消息并打印回复。

    Args:
        message: User message text to send. / 要发送的用户消息文本。
        stream: True to stream chunks, else print the full reply.
            / 为 True 流式输出，否则打印完整回复。
    """
    provider = create_model_provider()
    model = provider.get_chat_model()
    messages = [("system", "你是一个可靠的智能助手。"), ("human", message)]

    if stream:
        async for chunk in model.astream(messages):
            print(chunk.text, end="", flush=True)
        print()
    else:
        response = await model.ainvoke(messages)
        print(response.text)


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args.message, args.stream))
