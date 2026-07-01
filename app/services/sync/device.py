import platform
import socket
import uuid
from pathlib import Path

from app.config import CLIENT_DEVICE_ID, DATA_DIR


def get_or_create_device_id() -> str:
    if CLIENT_DEVICE_ID:
        return CLIENT_DEVICE_ID
    path = DATA_DIR / "client-device-id"
    if path.is_file():
        device_id = path.read_text(encoding="utf-8").strip()
        if device_id:
            return device_id
    device_id = str(uuid.uuid4())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(device_id, encoding="utf-8")
    return device_id


def device_hostname() -> str:
    return socket.gethostname()


def device_os() -> str:
    return platform.system()
