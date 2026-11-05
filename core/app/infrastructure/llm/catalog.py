"""Public model capabilities and privately configured vendor connections."""

from pydantic import BaseModel, ValidationError

from app.config import LLMSettings

VENDOR_MODELS = {
    "deepseek": ("deepseek-flash", "deepseek-v4-pro"),
    "mimo": ("mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed"),
}
EFFORTS = {"deepseek": ("none", "low", "high", "max"), "mimo": ("none", "high")}
OLDER_MODELS = {"deepseek": ("deepseek-v4-flash",), "mimo": ("mimo-v2.5", "mimo-v2.5-pro")}


class ModelItem(BaseModel):
    id: str
    provider: str
    display_name: str | None = None
    context_window: int | None = None
    reasoning_levels: tuple[str, ...] = ()
    default_effort: str | None = None


def configured_models() -> dict[str, tuple[ModelItem, LLMSettings]]:
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
