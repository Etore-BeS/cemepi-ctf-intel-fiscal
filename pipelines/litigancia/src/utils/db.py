import logging
import os

import boto3
from botocore.errorfactory import ClientError

from utils.settings import Settings

logger = logging.getLogger(__name__)


class ObjectStore:
    def __init__(self):
        self.path = Settings().object_store_path

    def make_dir(self):
        os.makedirs(self.path, exist_ok=True)

    def save_dir(self, file_name, data):
        self.make_dir()
        with open(os.path.join(self.path, file_name), "wb") as f:
            f.write(data)

    def save_zip(self, file_name, data):
        self.make_dir()
        with open(os.path.join(self.path, file_name), "wb") as f:
            f.write(data)


class S3:
    def __init__(self):
        self.settings = Settings()
        self.s3 = boto3.client(
            "s3",
            region_name=self.settings.aws_region,
            aws_access_key_id=self.settings.aws_access_key_id,
            aws_secret_access_key=self.settings.aws_secret_access_key,
        )
        self.bucket_name = self.settings.s3_bucket

    def ensure_bucket(self):
        try:
            self.s3.head_bucket(Bucket=self.bucket_name)
            logger.info("Bucket %s exists.", self.bucket_name)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "404":
                logger.info("Bucket %s not found. Creating...", self.bucket_name)
                try:
                    if self.settings.aws_region == "us-east-1":
                        self.s3.create_bucket(Bucket=self.bucket_name)
                    else:
                        self.s3.create_bucket(
                            Bucket=self.bucket_name,
                            CreateBucketConfiguration={
                                "LocationConstraint": self.settings.aws_region
                            },
                        )
                    logger.info("Bucket %s created successfully.", self.bucket_name)
                except ClientError as create_error:
                    logger.error("Failed to create bucket: %s", create_error)
                    raise
            else:
                logger.error("Error checking bucket: %s", e)
                raise
        return self.s3

    def upload_file(self, file_name, data):
        self.ensure_bucket()
        try:
            self.s3.put_object(Bucket=self.bucket_name, Key=file_name, Body=data)
            logger.info("File %s uploaded to S3 bucket %s", file_name, self.bucket_name)
        except ClientError as e:
            logger.error("Failed to upload file to S3: %s", e)
            raise

    def upload_file_path(self, file_path, s3_key=None, make_public=False):
        self.ensure_bucket()
        if s3_key is None:
            s3_key = os.path.basename(file_path)

        extra_args = {}
        if make_public:
            extra_args["ACL"] = "public-read"

        try:
            self.s3.upload_file(
                file_path,
                self.bucket_name,
                s3_key,
                ExtraArgs=extra_args,
            )
            logger.info(
                "File %s uploaded to S3 as %s (Public: %s)",
                file_path,
                s3_key,
                make_public,
            )
        except ClientError as e:
            logger.error("Failed to upload file to S3: %s", e)
            raise

    def get_public_url(self, object_name):
        return (
            f"https://{self.bucket_name}.s3.{self.settings.aws_region}"
            f".amazonaws.com/{object_name}"
        )
