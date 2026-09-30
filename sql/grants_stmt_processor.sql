-- Run ONCE as an admin. The Python processor's own DB user:
-- reads the Nest tables it needs, writes only its own stmt_* tables.
-- Replace the password and the database name (cellipay) if yours differs.
CREATE USER IF NOT EXISTS 'stmt_processor'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

GRANT SELECT ON cellipay.users   TO 'stmt_processor'@'%';
GRANT SELECT ON cellipay.devices TO 'stmt_processor'@'%';
GRANT SELECT ON cellipay.shops   TO 'stmt_processor'@'%';
GRANT SELECT ON cellipay.sales_agents TO 'stmt_processor'@'%';

GRANT SELECT, INSERT, UPDATE, DELETE ON cellipay.stmt_inbound_emails     TO 'stmt_processor'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON cellipay.stmt_requests           TO 'stmt_processor'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON cellipay.stmt_statements         TO 'stmt_processor'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON cellipay.stmt_transactions       TO 'stmt_processor'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON cellipay.stmt_merchants          TO 'stmt_processor'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON cellipay.stmt_assessments        TO 'stmt_processor'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON cellipay.stmt_assessment_devices TO 'stmt_processor'@'%';
FLUSH PRIVILEGES;