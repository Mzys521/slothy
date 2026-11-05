"""Run from core/: python -m examples.chat '你好' [--stream]."""

import argparse
import asyncio

from app.infrastructure.llm import create_model_provider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Call the configured model provider")
    parser.add_argument("message", nargs="?", default="请用一句话介绍你自己。")
    parser.add_argument("--stream", action="store_true")
    return parser.parse_args()


async def main(message: str, stream: bool) -> None:
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
