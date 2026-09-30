-- Adds BORROWED to stmt_transactions.bucket. Loan money in (Fuliza, M-Shwari,
-- loan apps) must never be counted as income, so it gets its own bucket.
ALTER TABLE stmt_transactions
  MODIFY bucket ENUM('INCOME','SELF_TRANSFER','REFUND','BORROWED','NEED','COMMITTED','WANT','FEE','UNKNOWN')
  NOT NULL COMMENT 'Drives the affordability maths';