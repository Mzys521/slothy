"""Public model capabilities derived from privately configured vendor connections.
/ 由私有配置的厂商连接生成的公开模型能力目录。"""

from pydantic import BaseModel, ValidationError

from app.config import LLMSettings

VENDOR_MODELS = {
    "deepseek": ("deepseek-flash", "deepseek-v4-pro"),
    "mimo": ("mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed"),
}
EFFORTS = {"deepseek": ("none", "low", "high", "max"), "mimo": ("none", "high")}
OLDER_MODELS = {"deepseek": ("deepseek-v4-flash",), "mimo": ("mimo-v2.5", "mimo-v2.5-pro")}


class ModelItem(BaseModel):
    """Public description of one selectable model.
    / 一个可选模型的公开描述。"""

    id: str
    provider: str
    display_name: str | None = None
    context_window: int | None = None
    reasoning_levels: tuple[str, ...] = ()
    default_effort: str | None = None


def configured_models() -> dict[str, tuple[ModelItem, LLMSettings]]:
    """Build the environment-configured catalog without exposing credentials.

    Every readable vendor connection contributes its configured model plus the known
    vendor models; invalid or missing vendor environments are skipped. Known models get
    the documented context window and reasoning levels, and force stream usage so token
    accounting stays accurate. / 每个可读的厂商连接贡献其配置模型与已知厂商模型；无效或缺失的
    厂商环境会被跳过。已知模型使用文档化的上下文窗口与推理档位，并强制流式用量以保证计数准确。

    Returns:
        Mapping of model id to its public description and per-model settings.
        / 模型 ID 到公开描述及该模型专用配置的映射。
    """
    try:
        primary = LLMSettings()
    except (ValidationError, ValueError):
        primary = None
    connections = [primary] if primary else []
    for vendor, models in VENDOR_MODELS.items():
        if primary and primary.provider == vendor:
            continue
        try:
            connection = LLMSettings(provider=vendor, vendor_credentials_only=True)
        except (ValidationError, ValueError):
            try:
                connection = LLMSettings(
                    provider=vendor, model=models[0], vendor_credentials_only=True,
                )
            except (ValidationError, ValueError):
                continue
        connections.append(connection)

    catalog = {}
    for connection in connections:
        vendor = connection.provider
        for model in dict.fromkeys((connection.model, *VENDOR_MODELS.get(vendor, ()))):
            known = model in (*VENDOR_MODELS.get(vendor, ()), *OLDER_MODELS.get(vendor, ()))
            catalog.setdefault(model, (
                ModelItem(
                    id=model, provider=vendor, context_window=1_048_576 if known else None,
                    reasoning_levels=EFFORTS[vendor] if known else (),
                    default_effort="none" if known else None,
                ),
                connection.model_copy(update={
                    "model": model, "stream_usage": known or connection.stream_usage,
                }),
            ))
    return catalog
