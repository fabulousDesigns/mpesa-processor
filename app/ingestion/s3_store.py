"""S3 layout in cellipay-mpesa-statements (bucket default encryption applies):

  emails/<ses-message-id>        raw .eml, written by SES itself
  attachments/<email-id>.pdf     the Safaricom PDF, still password protected
"""
from app.core.aws import s3
from app.core.config import get_settings


def get_object(bucket: str, key: str) -> bytes:
    return s3().get_object(Bucket=bucket, Key=key)["Body"].read()


def put_attachment(email_id: str, pdf: bytes) -> str:
    key = f"attachments/{email_id}.pdf"
    s3().put_object(Bucket=get_settings().statements_bucket, Key=key, Body=pdf,
                    ContentType="application/pdf")
    return key


def fetch_attachment(email_row: dict) -> bytes:
    """Matcher's FetchAttachment: the encrypted PDF for a stmt_inbound_emails row."""
    return get_object(email_row["s3_bucket"], email_row["attachment_s3_key"])