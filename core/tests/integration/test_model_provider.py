"""Exercise the real LangChain/SDK wire format without network calls."""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage
from openai import AuthenticationError
from pydantic import BaseModel

from app.config import LLMSettings
from app.infrastructure.llm import OpenAICompatibleProvider, create_model_provider


def completion_response(content: str = "你好") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "configured-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
    }


@pytest.mark.parametrize(
    ("provider_name", "expected_url"),
    [
        ("deepseek", "https://api.deepseek.com/chat/completions"),
        ("mimo", "https://api.xiaomimimo.com/v1/chat/completions"),
        ("openai_compatible", "https://gateway.example.test/v1/chat/completions"),
    ],
)
def test_sync_call_uses_configured_chat_completions_endpoint(
    provider_name: str, expected_url: str
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=completion_response())

    settings = LLMSettings(
        provider=provider_name,
        model="configured-model",
        api_key="test-key",
        base_url="https://gateway.example.test/v1"
        if provider_name == "openai_compatible"
        else None,
        temperature=0.4,
        max_tokens=200,
        extra_body={"thinking": {"type": "disabled"}},
        _env_file=None,
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = OpenAICompatibleProvider(settings, http_client=client)
        model = provider.get_chat_model()
        assert model is provider.get_chat_model()
        response = model.invoke([("system", "Be helpful"), ("human", "你好")])

    assert isinstance(response, AIMessage)
    assert response.text == "你好"
    assert response.usage_metadata["total_tokens"] == 10
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == expected_url
    assert request.headers["authorization"] == "Bearer test-key"
    payload = json.loads(request.content)
    assert payload["model"] == "configured-model"
    assert payload["messages"] == [
        {"role": "system", "content": "Be helpful"},
        {"role": "user", "content": "你好"},
    ]
    assert payload["temperature"] == 0.4
    limit_parameter = "max_tokens" if provider_name == "deepseek" else "max_completion_tokens"
    other_parameter = "max_completion_tokens" if provider_name == "deepseek" else "max_tokens"
    assert payload[limit_parameter] == 200
    assert other_parameter not in payload
    assert payload["thinking"] == {"type": "disabled"}
    assert "extra_body" not in payload


async def test_async_call_omits_unspecified_sampling_parameters() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=completion_response("异步回答"))

    settings = LLMSettings(
        provider="mimo", model="configured-model", api_key="test-key", _env_file=None
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        model = OpenAICompatibleProvider(settings, http_async_client=client).get_chat_model()
        response = await model.ainvoke("你好")

    assert response.text == "异步回答"
    payload = json.loads(requests[0].content)
    assert "temperature" not in payload
    assert "max_completion_tokens" not in payload
    assert "max_tokens" not in payload


@pytest.mark.parametrize("stream_usage", [False, True])
async def test_async_stream_parses_sse_and_respects_usage_option(stream_usage: bool) -> None:
    requests: list[httpx.Request] = []
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "你"}}]},
        {"choices": [{"index": 0, "delta": {"content": "好"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    if stream_usage:
        chunks.append(
            {
                "choices": [],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            }
        )
    event_stream = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    event_stream += "data: [DONE]\n\n"

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=event_stream)

    settings = LLMSettings(
        provider="mimo",
        model="configured-model",
        api_key="test-key",
        stream_usage=stream_usage,
        _env_file=None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        model = OpenAICompatibleProvider(settings, http_async_client=client).get_chat_model()
        received = [chunk async for chunk in model.astream("你好")]

    assert "".join(chunk.text for chunk in received) == "你好"
    payload = json.loads(requests[0].content)
    assert payload["stream"] is True
    if stream_usage:
        assert payload["stream_options"] == {"include_usage": True}
        combined = received[0]
        for chunk in received[1:]:
            combined += chunk
        assert combined.usage_metadata["total_tokens"] == 10
    else:
        assert "stream_options" not in payload


def test_tool_binding_preserves_standard_tool_calls() -> None:
    requests: list[httpx.Request] = []

    class GetWeather(BaseModel):
        """Get the weather for a city."""

        city: str

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = completion_response("")
        body["choices"][0] = {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-test",
                        "type": "function",
                        "function": {"name": "GetWeather", "arguments": '{"city":"Shanghai"}'},
                    }
                ],
            },
        }
        return httpx.Response(200, json=body)

    settings = LLMSettings(
        provider="deepseek", model="deepseek-flash", api_key="test-key",
        max_tokens=200, extra_body={"thinking": {"type": "enabled"}}, _env_file=None,
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = OpenAICompatibleProvider(settings, http_client=client).get_chat_model()
        response = model.bind_tools([GetWeather]).invoke("上海天气怎么样？")
        model.invoke("你好")

    assert response.tool_calls[0]["name"] == "GetWeather"
    assert response.tool_calls[0]["args"] == {"city": "Shanghai"}
    payload = json.loads(requests[0].content)
    assert payload["tools"][0]["function"]["name"] == "GetWeather"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 200
    assert json.loads(requests[1].content)["thinking"] == {"type": "enabled"}
    assert requests[0].url.path == "/chat/completions"


def test_authentication_errors_remain_visible_to_application() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "Invalid API key", "type": "authentication_error"}}
        )

    settings = LLMSettings(
        provider="deepseek", model="configured-model", api_key="test-key", _env_file=None
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = OpenAICompatibleProvider(settings, http_client=client).get_chat_model()
        with pytest.raises(AuthenticationError):
            model.invoke("你好")


def test_json_mode_validates_structured_output() -> None:
    requests: list[httpx.Request] = []

    class Preference(BaseModel):
        language: str

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=completion_response('{"language":"Python"}'))

    settings = LLMSettings(
        provider="deepseek", model="configured-model", api_key="test-key", _env_file=None
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = OpenAICompatibleProvider(settings, http_client=client).get_chat_model()
        extractor = model.with_structured_output(Preference, method="json_mode")
        result = extractor.invoke('Return JSON with the user preference: {"language":"Python"}')

    assert result == Preference(language="Python")
    assert json.loads(requests[0].content)["response_format"] == {"type": "json_object"}


def test_factory_supports_multiple_independent_models() -> None:
    deepseek = create_model_provider(
        LLMSettings(
            provider="deepseek", model="chat-model", api_key="chat-test-key", _env_file=None
        )
    )
    mimo = create_model_provider(
        LLMSettings(
            provider="mimo", model="extract-model", api_key="extract-test-key", _env_file=None
        )
    )
    assert deepseek.get_chat_model() is not mimo.get_chat_model()
    assert deepseek.get_chat_model().model_name == "chat-model"
    assert mimo.get_chat_model().model_name == "extract-model"
