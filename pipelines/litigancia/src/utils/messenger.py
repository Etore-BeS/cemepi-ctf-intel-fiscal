import json
from datetime import datetime
from enum import Enum

import requests

from utils.settings import Settings


class Color(Enum):
    INFO = "#2196F3"
    SUCCESS = "#36a64f"
    WARNING = "#FFC107"
    ERROR = "#FF0000"


class Messenger:
    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url or Settings().slack_webhook_url

    def _send_payload(self, text: str, title: str, color: Color):
        if not self.webhook_url:
            print(f"[{title.upper()}] {text}")
            return

        payload = {
            "text": (
                f"{title} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} \n {text}"
            ),
            "username": "MedPlus Data Bot",
            "icon_emoji": ":robot_face:",
        }

        try:
            response = requests.post(
                self.webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=5,
            )
            response.raise_for_status()
            print(f"Slack message sent: {title}")
        except Exception as e:
            print(f"Failed to send Slack message: {e}")

    def info(self, text: str):
        self._send_payload(text, "Info", Color.INFO)

    def success(self, text: str):
        self._send_payload(text, "Success", Color.SUCCESS)

    def warning(self, text: str):
        self._send_payload(text, "Warning", Color.WARNING)

    def error(self, text: str):
        self._send_payload(text, "Error", Color.ERROR)
