"""Native web-search payload adaptation tests (no network). / 原生联网搜索载荷适配测试。"""

from langchain_core.messages import HumanMessage

from app.config import LLMSettings
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider


def payload(provider: str, model: str, web_search: str = "auto") -> dict:
    """Build one offline request payload for a configured provider. / 构造离线请求载荷。"""
    settings = LLMSettings(
        _env_file=None,
        provider=provider,
        model=model,
        api_key="test-key",
        base_url="https://api.example.com/v1",
        web_search=web_search,
    )
    return OpenAICompatibleProvider(settings).get_chat_model()._get_request_payload(
        [HumanMessage("hello")]
    )


def test_mimo_supported_model_enables_web_search_tool_by_default():
    """Supported MiMo models receive the native web_search tool.
    / 受支持 MiMo 模型默认带原生搜索。"""
    result = payload("mimo", "mimo-v2.6-flash")
    assert result["tools"] == [
        {"type": "web_search", "max_keyword": 3, "force_search": False, "limit": 1}
    ]
    assert result["tool_choice"] == "auto"
    assert "messages" in result


def test_mimo_unsupported_model_has_no_search_parameters():
    """Unsupported models are not given incompatible fields. / 不支持的模型不发送不兼容参数。"""
    result = payload("mimo", "mimo-legacy")
    assert "tools" not in result
    assert "input" not in result


def test_deepseek_never_adds_native_search_to_chat_requests():
    """DeepSeek uses the independent Search tool, not a chat-completions flag.
    / DeepSeek 使用独立 Search 工具，不在聊天请求中加搜索参数。"""
    result = payload("deepseek", "deepseek-v4-pro")
    assert "messages" in result
    assert "tools" not in result
    assert "input" not in result


def test_deepseek_regular_model_stays_on_chat_completions():
    """Other DeepSeek models keep the stable Chat Completions path.
    / 其他 DeepSeek 模型保持 Chat Completions。"""
    result = payload("deepseek", "deepseek-flash")
    assert "messages" in result
    assert "tools" not in result


def test_off_mode_disables_native_search_for_supported_model():
    """An explicit off policy always disables search. / 显式 off 策略禁用搜索。"""
    result = payload("mimo", "mimo-v2.6-flash", web_search="off")
    assert "tools" not in result
