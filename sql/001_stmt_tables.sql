-- ============================================================================
-- CelliPay statement scoring: stmt_ tables (owned by the Python processor)
-- MariaDB 10.6+ / InnoDB / utf8mb4
--
-- BEFORE RUNNING: check the collation of the Nest tables and make the
-- COLLATE below match, otherwise the foreign keys to users/shops/devices
-- will fail with errno 150:
--     SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES
--     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN
--       ('users','shops','sales_agents','devices','sales');
--
-- Phone numbers are stored as msisdn CHAR(12) with no plus: 254110026199.
-- users.phoneNumber is '+254...', so the join is CONCAT('+', msisdn).
-- ============================================================================

-- 1) Every Safaricom statement email as it lands ------------------------------
CREATE TABLE stmt_inbound_emails (
  id                     VARCHAR(36)   NOT NULL,
  ses_message_id         VARCHAR(255)  NOT NULL COMMENT 'Dedupe key. SES/SNS can deliver twice.',
  s3_bucket              VARCHAR(255)  NOT NULL,
  s3_key                 VARCHAR(1024) NOT NULL COMMENT 'Raw .eml as SES stored it',
  from_address           VARCHAR(255)  NULL,
  subject                VARCHAR(500)  NULL,
  received_at            DATETIME(3)   NOT NULL COMMENT 'When SES received it (EAT stored as UTC)',
  attachment_filename    VARCHAR(255)  NULL,
  attachment_s3_key      VARCHAR(1024) NULL COMMENT 'Encrypted PDF extracted from the email',
  attachment_sha256      CHAR(64)      NULL,
  msisdn                 CHAR(12)      NULL COMMENT 'Full number from the attachment filename',
  msisdn_masked          VARCHAR(20)   NULL COMMENT 'From subject, e.g. 254110***199. Fallback only.',
  requested_time         TIME          NULL COMMENT 'The "requested at HH:MM:SS" from the subject',
  period_start           DATE          NULL,
  period_end             DATE          NULL,
  status                 ENUM('UNCLAIMED','CLAIMED','UNPARSEABLE','EXPIRED','PURGED')
                                       NOT NULL DEFAULT 'UNCLAIMED',
  status_reason          VARCHAR(255)  NULL,
  claimed_by_request_id  VARCHAR(36)   NULL,
  claimed_at             DATETIME(3)   NULL,
  purged_at              DATETIME(3)   NULL,
  created_at             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY UQ_STMT_EMAIL_MESSAGE (ses_message_id),
  KEY IDX_STMT_EMAIL_MATCH (msisdn, status, received_at),
  KEY IDX_STMT_EMAIL_MASKED (msisdn_masked, status, received_at),
  KEY IDX_STMT_EMAIL_STATUS_RECEIVED (status, received_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 2) The agent's request: "open this customer's statement with this code" ---
CREATE TABLE stmt_requests (
  id                     VARCHAR(36)   NOT NULL,
  user_id                VARCHAR(36)   NOT NULL,
  agent_id               VARCHAR(36)   NULL COMMENT 'sales_agents.id',
  shop_id                VARCHAR(36)   NULL,
  msisdn                 CHAR(12)      NOT NULL COMMENT 'Customer number, normalised, no plus',
  code_ciphertext        VARBINARY(512) NULL COMMENT 'Encrypted 6-digit code. Wiped (NULL) once the PDF opens.',
  code_submitted_at      DATETIME(3)   NULL,
  sms_requested_time     TIME          NULL COMMENT 'Optional: "requested at" from the SMS, used as a tie-breaker',
  status                 ENUM('AWAITING_CODE','AWAITING_EMAIL','WRONG_CODE','LOCKED_OUT',
                              'OPENED','PARSED','SCORED','FAILED','EXPIRED','CANCELLED')
                                       NOT NULL DEFAULT 'AWAITING_CODE',
  failure_reason         VARCHAR(255)  NULL,
  code_attempts          INT UNSIGNED  NOT NULL DEFAULT 0,
  max_code_attempts      INT UNSIGNED  NOT NULL DEFAULT 5,
  email_id               VARCHAR(36)   NULL COMMENT 'Set when an email is claimed',
  sale_id                VARCHAR(36)   NULL COMMENT 'Linked when the sale is created',
  consent_at             DATETIME(3)   NOT NULL COMMENT 'Customer consent to scoring + collections use',
  consent_version        VARCHAR(20)   NOT NULL,
  expires_at             DATETIME(3)   NOT NULL,
  created_at             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  -- Only one live request per customer at a time. Finished rows become NULL here,
  -- and MariaDB allows many NULLs in a unique index.
  live_user_id           VARCHAR(36) AS (
                           IF(status IN ('AWAITING_CODE','AWAITING_EMAIL','WRONG_CODE','OPENED','PARSED'),
                              user_id, NULL)
                         ) PERSISTENT,
  PRIMARY KEY (id),
  UNIQUE KEY UQ_STMT_REQ_ONE_LIVE_PER_USER (live_user_id),
  KEY IDX_STMT_REQ_MATCH (msisdn, status, created_at),
  KEY IDX_STMT_REQ_USER (user_id, created_at),
  KEY IDX_STMT_REQ_AGENT (agent_id, created_at),
  KEY IDX_STMT_REQ_EXPIRES (status, expires_at),
  CONSTRAINT FK_STMT_REQ_USER  FOREIGN KEY (user_id)  REFERENCES users (id),
  CONSTRAINT FK_STMT_REQ_AGENT FOREIGN KEY (agent_id) REFERENCES sales_agents (id),
  CONSTRAINT FK_STMT_REQ_SHOP  FOREIGN KEY (shop_id)  REFERENCES shops (id),
  CONSTRAINT FK_STMT_REQ_SALE  FOREIGN KEY (sale_id)  REFERENCES sales (id),
  CONSTRAINT FK_STMT_REQ_EMAIL FOREIGN KEY (email_id) REFERENCES stmt_inbound_emails (id),
  CONSTRAINT CK_STMT_REQ_MSISDN CHECK (msisdn REGEXP '^254[17][0-9]{8}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE stmt_inbound_emails
  ADD CONSTRAINT FK_STMT_EMAIL_CLAIMED_BY FOREIGN KEY (claimed_by_request_id) REFERENCES stmt_requests (id);


-- 3) The opened statement: header + reconciliation --------------------------
CREATE TABLE stmt_statements (
  id                     VARCHAR(36)   NOT NULL,
  request_id             VARCHAR(36)   NOT NULL,
  email_id               VARCHAR(36)   NOT NULL,
  customer_name          VARCHAR(255)  NOT NULL COMMENT 'As printed on page 1',
  msisdn                 CHAR(12)      NOT NULL COMMENT 'Page 1 number, normalised',
  statement_email        VARCHAR(255)  NULL,
  period_start           DATE          NOT NULL,
  period_end             DATE          NOT NULL,
  request_date           DATE          NULL,
  first_txn_at           DATETIME      NULL,
  last_txn_at            DATETIME      NULL,
  window_days            DECIMAL(7,2)  NOT NULL COMMENT 'Period length used for all per-day numbers',
  page_count             SMALLINT UNSIGNED NULL,
  txn_count              INT UNSIGNED  NOT NULL DEFAULT 0,
  summary_paid_in        DECIMAL(15,2) NULL COMMENT 'Safaricom page-1 TOTAL',
  summary_paid_out       DECIMAL(15,2) NULL,
  parsed_paid_in         DECIMAL(15,2) NULL COMMENT 'Sum of our parsed rows',
  parsed_paid_out        DECIMAL(15,2) NULL,
  reconciled             TINYINT(1)    NOT NULL DEFAULT 0 COMMENT '1 only if parsed totals equal summary to the cent',
  verification_code      VARCHAR(20)   NULL COMMENT 'Safaricom *334# statement verification code',
  name_matched           TINYINT(1)    NOT NULL DEFAULT 0 COMMENT 'Page-1 name vs users first/last name',
  msisdn_matched         TINYINT(1)    NOT NULL DEFAULT 0,
  decrypted_s3_key       VARCHAR(1024) NULL COMMENT 'Decrypted copy, SSE-KMS, restricted prefix',
  parser_version         VARCHAR(20)   NOT NULL,
  created_at             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY UQ_STMT_STATEMENT_REQUEST (request_id),
  UNIQUE KEY UQ_STMT_STATEMENT_EMAIL (email_id),
  KEY IDX_STMT_STATEMENT_MSISDN (msisdn, created_at),
  CONSTRAINT FK_STMT_STATEMENT_REQUEST FOREIGN KEY (request_id) REFERENCES stmt_requests (id),
  CONSTRAINT FK_STMT_STATEMENT_EMAIL   FOREIGN KEY (email_id)   REFERENCES stmt_inbound_emails (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 4) Merchant registry: shared across ALL customers (the collections asset) --
CREATE TABLE stmt_merchants (
  id                     VARCHAR(36)   NOT NULL,
  merchant_key           VARCHAR(191)  NOT NULL COMMENT 'TILL:4748597 | PAYBILL:4005495 | POCHI:254726***245:JOSEPH GACHARA',
  kind                   ENUM('TILL','PAYBILL','POCHI','CARD_MERCHANT') NOT NULL,
  number                 VARCHAR(30)   NULL COMMENT 'Till/paybill number, or masked number for pochi',
  name                   VARCHAR(255)  NOT NULL COMMENT 'Most recent name seen on statements',
  category               VARCHAR(50)   NULL COMMENT 'e.g. GROCERIES, FOOD_DELIVERY, BANK, INTERNET',
  location_text          VARCHAR(255)  NULL COMMENT 'e.g. KINOO, UTHIRU, SPUR MALL',
  county                 VARCHAR(50)   NULL,
  latitude               DECIMAL(10,7) NULL,
  longitude              DECIMAL(10,7) NULL,
  location_source        ENUM('NAME','MANUAL','GEOCODED') NULL,
  location_verified      TINYINT(1)    NOT NULL DEFAULT 0,
  first_seen_at          DATETIME      NULL,
  last_seen_at           DATETIME      NULL,
  txn_count              INT UNSIGNED  NOT NULL DEFAULT 0,
  customer_count         INT UNSIGNED  NOT NULL DEFAULT 0,
  created_at             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY UQ_STMT_MERCHANT_KEY (merchant_key),
  KEY IDX_STMT_MERCHANT_KIND_NUMBER (kind, number),
  KEY IDX_STMT_MERCHANT_CATEGORY (category),
  KEY IDX_STMT_MERCHANT_COUNTY (county)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 5) Every statement line, classified ----------------------------------------
CREATE TABLE stmt_transactions (
  id                     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  statement_id           VARCHAR(36)   NOT NULL,
  line_no                INT UNSIGNED  NOT NULL COMMENT 'Order in the PDF. Receipts repeat on charge rows, so this is the real key.',
  receipt_no             VARCHAR(20)   NOT NULL,
  completed_at           DATETIME      NOT NULL,
  details                TEXT          NOT NULL COMMENT 'Raw Details text, whitespace-normalised',
  txn_status             VARCHAR(20)   NOT NULL,
  paid_in                DECIMAL(15,2) NOT NULL DEFAULT 0,
  withdrawn              DECIMAL(15,2) NOT NULL DEFAULT 0 COMMENT 'Positive number',
  balance                DECIMAL(15,2) NULL,
  direction              ENUM('IN','OUT') NOT NULL,
  txn_type               VARCHAR(40)   NOT NULL COMMENT 'Mechanics: P2P_SEND, P2P_RECEIVE, PAYBILL, TILL, POCHI, B2C, AIRTIME, BUNDLE, CARD, CHARGE, REVERSAL, AGENT_DEPOSIT, AGENT_WITHDRAWAL, OTHER',
  bucket                 ENUM('INCOME','SELF_TRANSFER','REFUND','NEED','COMMITTED','WANT','FEE','UNKNOWN')
                                       NOT NULL COMMENT 'Drives the affordability maths',
  category               VARCHAR(50)   NOT NULL COMMENT 'Meaning: SALARY, GROCERIES, FOOD_DELIVERY, BANK_TRANSFER, LOAN_APP, BETTING, ...',
  counterparty_name      VARCHAR(255)  NULL,
  counterparty_number    VARCHAR(30)   NULL COMMENT 'Masked as printed, or till/paybill number',
  account_ref            VARCHAR(100)  NULL COMMENT 'Paybill account, e.g. Glovo',
  merchant_id            VARCHAR(36)   NULL,
  rule_id                VARCHAR(50)   NULL COMMENT 'Which classifier rule matched, for debugging',
  PRIMARY KEY (id),
  UNIQUE KEY UQ_STMT_TXN_LINE (statement_id, line_no),
  KEY IDX_STMT_TXN_RECEIPT (receipt_no),
  KEY IDX_STMT_TXN_STATEMENT_BUCKET (statement_id, bucket),
  KEY IDX_STMT_TXN_STATEMENT_CATEGORY (statement_id, category),
  KEY IDX_STMT_TXN_MERCHANT_TIME (merchant_id, completed_at),
  CONSTRAINT FK_STMT_TXN_STATEMENT FOREIGN KEY (statement_id) REFERENCES stmt_statements (id) ON DELETE CASCADE,
  CONSTRAINT FK_STMT_TXN_MERCHANT  FOREIGN KEY (merchant_id)  REFERENCES stmt_merchants (id),
  CONSTRAINT CK_STMT_TXN_ONE_SIDE CHECK ((paid_in > 0 AND withdrawn = 0) OR (paid_in = 0 AND withdrawn > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 6) The score -----------------------------------------------------------------
CREATE TABLE stmt_assessments (
  id                     VARCHAR(36)   NOT NULL,
  statement_id           VARCHAR(36)   NOT NULL,
  request_id             VARCHAR(36)   NOT NULL,
  user_id                VARCHAR(36)   NOT NULL,
  window_days            DECIMAL(7,2)  NOT NULL,
  income_total           DECIMAL(15,2) NOT NULL,
  self_transfer_total    DECIMAL(15,2) NOT NULL,
  income_daily           DECIMAL(12,2) NOT NULL,
  needs_daily            DECIMAL(12,2) NOT NULL,
  committed_daily        DECIMAL(12,2) NOT NULL,
  wants_daily            DECIMAL(12,2) NOT NULL,
  fees_daily             DECIMAL(12,2) NOT NULL,
  left_daily             DECIMAL(12,2) NOT NULL COMMENT 'Can be negative',
  affordability_ratio    DECIMAL(4,3)  NOT NULL COMMENT 'Share of left_daily allowed for a device, e.g. 0.900',
  affordable_daily       DECIMAL(12,2) NOT NULL COMMENT 'max(0, left_daily * ratio). The number devices are compared to.',
  min_balance            DECIMAL(15,2) NULL,
  avg_balance            DECIMAL(15,2) NULL,
  has_fuliza             TINYINT(1)    NOT NULL DEFAULT 0,
  has_loan_apps          TINYINT(1)    NOT NULL DEFAULT 0,
  has_betting            TINYINT(1)    NOT NULL DEFAULT 0,
  confidence             ENUM('LOW','MEDIUM','HIGH') NOT NULL,
  flags                  JSON          NULL COMMENT 'e.g. ["SHORT_WINDOW_7D","SELF_TRANSFER_HEAVY"]',
  category_breakdown     JSON          NULL COMMENT 'Per category totals and per-day values',
  engine_version         VARCHAR(20)   NOT NULL,
  created_at             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY UQ_STMT_ASSESSMENT_STATEMENT (statement_id),
  KEY IDX_STMT_ASSESSMENT_USER (user_id, created_at),
  CONSTRAINT FK_STMT_ASSESSMENT_STATEMENT FOREIGN KEY (statement_id) REFERENCES stmt_statements (id),
  CONSTRAINT FK_STMT_ASSESSMENT_REQUEST   FOREIGN KEY (request_id)   REFERENCES stmt_requests (id),
  CONSTRAINT FK_STMT_ASSESSMENT_USER      FOREIGN KEY (user_id)      REFERENCES users (id),
  CONSTRAINT CK_STMT_ASSESSMENT_FLAGS     CHECK (flags IS NULL OR JSON_VALID(flags)),
  CONSTRAINT CK_STMT_ASSESSMENT_BREAKDOWN CHECK (category_breakdown IS NULL OR JSON_VALID(category_breakdown))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 7) Frozen snapshot of what each device looked like at scoring time ----------
CREATE TABLE stmt_assessment_devices (
  id                     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  assessment_id          VARCHAR(36)   NOT NULL,
  device_id              VARCHAR(36)   NOT NULL,
  shop_id                VARCHAR(36)   NULL,
  brand                  VARCHAR(100)  NULL,
  model                  VARCHAR(100)  NULL,
  daily_payment          DECIMAL(10,2) NOT NULL COMMENT 'PriceBuilder.dailyPayment at scoring time',
  credit_multiplier      DECIMAL(3,1)  NOT NULL,
  required_daily         DECIMAL(12,2) NOT NULL COMMENT 'daily_payment * credit_multiplier',
  deposit_required       DECIMAL(15,2) NOT NULL COMMENT 'PriceBuilder.actualDepositRequired',
  qualified              TINYINT(1)    NOT NULL,
  headroom_daily         DECIMAL(12,2) NOT NULL COMMENT 'affordable_daily - required_daily. Negative = how far off.',
  created_at             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY UQ_STMT_ASSESSMENT_DEVICE (assessment_id, device_id),
  KEY IDX_STMT_ASSESSMENT_DEVICE_QUALIFIED (assessment_id, qualified, required_daily),
  CONSTRAINT FK_STMT_AD_ASSESSMENT FOREIGN KEY (assessment_id) REFERENCES stmt_assessments (id) ON DELETE CASCADE,
  CONSTRAINT FK_STMT_AD_DEVICE     FOREIGN KEY (device_id)     REFERENCES devices (id),
  CONSTRAINT FK_STMT_AD_SHOP       FOREIGN KEY (shop_id)       REFERENCES shops (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
