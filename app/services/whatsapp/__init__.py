__all__ = ["WhatsAppService"]


def __getattr__(name: str):
    if name == "WhatsAppService":
        from app.services.whatsapp.service import WhatsAppService

        return WhatsAppService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
