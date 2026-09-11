import json
import os

from dotenv import load_dotenv

from config.paths import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")


class Settings:
    def __init__(self):
        self.slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        self.slack_channel = os.getenv("SLACK_CHANNEL")
        self.object_store_path = os.getenv("OBJECT_STORE_PATH")
        self.s3_bucket = os.getenv("S3_BUCKET")
        self.aws_region = os.getenv("AWS_DEFAULT_REGION")
        self.aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        self.aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        oxylabs_path = os.getenv("OXYLABS_PATH")
        if oxylabs_path:
            with open(oxylabs_path, encoding="utf-8") as f:
                self.oxylabs = json.load(f)
        else:
            self.oxylabs = {}
