"""One-time AWS setup for the statements inbox. Safe to re-run.

    python infra/setup_inbox.py --domain statements.yourdomain.net --region eu-west-1

Creates: private KMS-encrypted S3 bucket (auto-expiry) -> SNS topic -> SQS queue + DLQ,
verifies the domain in SES, and a receipt rule (spam/virus scan, save to S3, notify SNS).
Prints the DNS records to add and the .env values for the worker.
"""
import argparse
import json

import boto3
from botocore.exceptions import ClientError

p = argparse.ArgumentParser()
p.add_argument("--domain", required=True, help="e.g. statements.cellipay.net")
p.add_argument("--region", default="eu-west-1", help="must support SES receiving")
p.add_argument("--mailbox", default="mpesa")
p.add_argument("--profile", default=None, help="AWS CLI profile (admin rights)")
a = p.parse_args()

s = boto3.Session(profile_name=a.profile, region_name=a.region)
acct = s.client("sts").get_caller_identity()["Account"]
s3, sns, sqs, ses = s.client("s3"), s.client("sns"), s.client("sqs"), s.client("ses")
inbox = f"{a.mailbox}@{a.domain}"
bucket = f"cellipay-mpesa-statements-{acct}"
ruleset, rule = "cellipay-inbound", "mpesa-statements"


def step(msg):
    print(f"\n==> {msg}")


step(f"S3 bucket {bucket}")
try:
    s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": a.region})
except ClientError as e:
    if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
        raise
s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
    "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={"Rules": [
    {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"}, "BucketKeyEnabled": True}]})
s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [
    {"ID": "raw-emails", "Filter": {"Prefix": "inbound/"}, "Status": "Enabled", "Expiration": {"Days": 30}},
    {"ID": "attachments", "Filter": {"Prefix": "attachments/"}, "Status": "Enabled", "Expiration": {"Days": 90}}]})
s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
    "Sid": "SESPut", "Effect": "Allow", "Principal": {"Service": "ses.amazonaws.com"},
    "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{bucket}/inbound/*",
    "Condition": {"StringEquals": {"AWS:SourceAccount": acct}}}]}))

step("SQS dead-letter queue + main queue")
dlq_url = sqs.create_queue(QueueName="cellipay-statements-dlq",
                           Attributes={"MessageRetentionPeriod": "1209600"})["QueueUrl"]
dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
q_url = sqs.create_queue(QueueName="cellipay-statements", Attributes={
    "VisibilityTimeout": "120", "MessageRetentionPeriod": "345600", "ReceiveMessageWaitTimeSeconds": "20",
    "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "5"})})["QueueUrl"]
q_arn = sqs.get_queue_attributes(QueueUrl=q_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

step("SNS topic -> queue (raw delivery)")
topic = sns.create_topic(Name="cellipay-statement-received")["TopicArn"]
sns.set_topic_attributes(TopicArn=topic, AttributeName="Policy", AttributeValue=json.dumps({
    "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ses.amazonaws.com"},
    "Action": "SNS:Publish", "Resource": topic, "Condition": {"StringEquals": {"AWS:SourceAccount": acct}}}]}))
sqs.set_queue_attributes(QueueUrl=q_url, Attributes={"Policy": json.dumps({
    "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"},
    "Action": "sqs:SendMessage", "Resource": q_arn, "Condition": {"ArnEquals": {"aws:SourceArn": topic}}}]})})
subs = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
if not any(x["Endpoint"] == q_arn for x in subs):
    sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=q_arn, Attributes={"RawMessageDelivery": "true"})

step(f"SES domain {a.domain}")
token = ses.verify_domain_identity(Domain=a.domain)["VerificationToken"]

step("SES receipt rule")
try:
    ses.create_receipt_rule_set(RuleSetName=ruleset)
except ClientError as e:
    if e.response["Error"]["Code"] != "AlreadyExists":
        raise
rule_def = {"Name": rule, "Enabled": True, "ScanEnabled": True, "TlsPolicy": "Require", "Recipients": [inbox],
            "Actions": [{"S3Action": {"BucketName": bucket, "ObjectKeyPrefix": "inbound/", "TopicArn": topic}}]}
try:
    ses.create_receipt_rule(RuleSetName=ruleset, Rule=rule_def)
except ClientError as e:
    if e.response["Error"]["Code"] != "AlreadyExists":
        raise
    ses.update_receipt_rule(RuleSetName=ruleset, Rule=rule_def)
ses.set_active_receipt_rule_set(RuleSetName=ruleset)

print(f"""
================================================================
DNS records to add at your .net DNS provider:

  TXT   _amazonses.{a.domain}    "{token}"
  MX    {a.domain}               10 inbound-smtp.{a.region}.amazonaws.com

Customers send statements to:   {inbox}

Worker .env:
  AWS_REGION={a.region}
  STATEMENTS_BUCKET={bucket}
  STATEMENTS_QUEUE_URL={q_url}
  STATEMENT_INBOX={inbox}

Check verification later with:
  aws ses get-identity-verification-attributes --identities {a.domain} --region {a.region}
================================================================""")