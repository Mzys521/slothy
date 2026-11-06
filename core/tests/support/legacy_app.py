"""Exercise legacy rule/review contracts with chat-only model fixtures.

The production/default LLM memory path is tested separately in test_llm_memory.py.
"""

from app.api import create_app as application


def create_app(**kwargs):
    return application(memory_processing="rules", **kwargs)
