"""Shared boto3 clients. No static keys: on EC2 these pick up the instance role via IMDS."""
from functools import lru_cache
import boto3
from app.core.config import get_settings


@lru_cache
def _session() -> boto3.session.Session:
    return boto3.session.Session(region_name=get_settings().aws_region)


def s3():
    return _session().client("s3")


def sqs():
    return _session().client("sqs")
