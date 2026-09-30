-- Rollback for 001. Drop order respects foreign keys.
ALTER TABLE stmt_inbound_emails DROP FOREIGN KEY FK_STMT_EMAIL_CLAIMED_BY;
DROP TABLE IF EXISTS stmt_assessment_devices;
DROP TABLE IF EXISTS stmt_assessments;
DROP TABLE IF EXISTS stmt_transactions;
DROP TABLE IF EXISTS stmt_merchants;
DROP TABLE IF EXISTS stmt_statements;
DROP TABLE IF EXISTS stmt_requests;
DROP TABLE IF EXISTS stmt_inbound_emails;
