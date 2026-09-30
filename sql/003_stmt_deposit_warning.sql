-- Per-device heads-up: deposit is more than the customer's average M-PESA balance.
ALTER TABLE stmt_assessment_devices
  ADD COLUMN deposit_above_avg_balance TINYINT(1) NOT NULL DEFAULT 0 AFTER deposit_required;
