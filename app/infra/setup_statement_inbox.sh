#!/usr/bin/env bash
# One-time AWS setup: SES inbound -> S3 (+ SNS) -> SQS (+ DLQ).
# Needs: aws cli v2 with admin creds, and access to cellipay.co.ke DNS.
set -euo pipefail

# ── edit these ───────────────────────────────────────────────────────────────
SES_REGION="eu-west-1"                      # a region where SES *receiving* is enabled
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
DOMAIN="statements.cellipay.co.ke"          # subdomain, so company email (M365) is untouched
INBOX="mpesa@${DOMAIN}"                      # what customers type into the M-PESA statement request
BUCKET="cellipay-mpesa-statements-${ACCOUNT_ID}"
TOPIC="cellipay-statement-received"
QUEUE="cellipay-statements"
DLQ="cellipay-statements-dlq"
RULESET="cellipay-inbound"
# ─────────────────────────────────────────────────────────────────────────────
export AWS_REGION="$SES_REGION"

echo "1) Bucket, private + KMS-encrypted, raw emails auto-deleted after 30 days"
aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint="$SES_REGION" || true
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"},"BucketKeyEnabled":true}]}'
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration '{
  "Rules":[
    {"ID":"raw-emails","Filter":{"Prefix":"inbound/"},"Status":"Enabled","Expiration":{"Days":30}},
    {"ID":"attachments","Filter":{"Prefix":"attachments/"},"Status":"Enabled","Expiration":{"Days":90}}]}'
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "{
  \"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"SESPut\",\"Effect\":\"Allow\",
  \"Principal\":{\"Service\":\"ses.amazonaws.com\"},\"Action\":\"s3:PutObject\",
  \"Resource\":\"arn:aws:s3:::${BUCKET}/inbound/*\",
  \"Condition\":{\"StringEquals\":{\"AWS:SourceAccount\":\"${ACCOUNT_ID}\"}}}]}"

echo "2) DLQ + queue (5 tries, then DLQ; messages kept 4 days)"
DLQ_URL=$(aws sqs create-queue --queue-name "$DLQ" --attributes MessageRetentionPeriod=1209600 --query QueueUrl --output text)
DLQ_ARN=$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)
Q_URL=$(aws sqs create-queue --queue-name "$QUEUE" --attributes \
  "{\"VisibilityTimeout\":\"120\",\"MessageRetentionPeriod\":\"345600\",\"ReceiveMessageWaitTimeSeconds\":\"20\",
    \"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${DLQ_ARN}\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"}" \
  --query QueueUrl --output text)
Q_ARN=$(aws sqs get-queue-attributes --queue-url "$Q_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)

echo "3) SNS topic -> queue (raw delivery, so the worker gets SES's JSON directly)"
TOPIC_ARN=$(aws sns create-topic --name "$TOPIC" --query TopicArn --output text)
aws sns set-topic-attributes --topic-arn "$TOPIC_ARN" --attribute-name Policy --attribute-value "{
  \"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"ses.amazonaws.com\"},
  \"Action\":\"SNS:Publish\",\"Resource\":\"${TOPIC_ARN}\",
  \"Condition\":{\"StringEquals\":{\"AWS:SourceAccount\":\"${ACCOUNT_ID}\"}}}]}"
aws sqs set-queue-attributes --queue-url "$Q_URL" --attributes "{\"Policy\":\"{\\\"Version\\\":\\\"2012-10-17\\\",
  \\\"Statement\\\":[{\\\"Effect\\\":\\\"Allow\\\",\\\"Principal\\\":{\\\"Service\\\":\\\"sns.amazonaws.com\\\"},
  \\\"Action\\\":\\\"sqs:SendMessage\\\",\\\"Resource\\\":\\\"${Q_ARN}\\\",
  \\\"Condition\\\":{\\\"ArnEquals\\\":{\\\"aws:SourceArn\\\":\\\"${TOPIC_ARN}\\\"}}}]}\"}"
aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol sqs --notification-endpoint "$Q_ARN" \
  --attributes RawMessageDelivery=true

echo "4) SES: verify the subdomain, then a receipt rule: spam/virus scan, save to S3, notify SNS"
aws ses verify-domain-identity --domain "$DOMAIN" --query VerificationToken --output text \
  | xargs -I{} echo "   DNS TXT  _amazonses.${DOMAIN}  ->  {}"
aws ses create-receipt-rule-set --rule-set-name "$RULESET" 2>/dev/null || true
aws ses create-receipt-rule --rule-set-name "$RULESET" --rule "{
  \"Name\":\"mpesa-statements\",\"Enabled\":true,\"ScanEnabled\":true,\"TlsPolicy\":\"Require\",
  \"Recipients\":[\"${INBOX}\"],
  \"Actions\":[{\"S3Action\":{\"BucketName\":\"${BUCKET}\",\"ObjectKeyPrefix\":\"inbound/\",\"TopicArn\":\"${TOPIC_ARN}\"}}]}"
aws ses set-active-receipt-rule-set --rule-set-name "$RULESET"

cat <<DONE

Done. Remaining by hand:
  DNS  MX   ${DOMAIN}  10 inbound-smtp.${SES_REGION}.amazonaws.com
  DNS  TXT  _amazonses.${DOMAIN}  (token printed above)

.env for the worker:
  AWS_REGION=${SES_REGION}
  STATEMENTS_BUCKET=${BUCKET}
  STATEMENTS_QUEUE_URL=${Q_URL}

Worker IAM role needs: s3:GetObject on ${BUCKET}/inbound/*, s3:GetObject+PutObject on ${BUCKET}/attachments/*,
sqs:ReceiveMessage/DeleteMessage/ChangeMessageVisibility on ${Q_ARN}, kms:Decrypt/GenerateDataKey on the S3 KMS key.
DONE