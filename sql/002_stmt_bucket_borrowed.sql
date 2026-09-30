-- Adds BORROWED to stmt_transactions.bucket. Loan money in must never count as income.
ALTER TABLE stmt_transactions
  MODIFY bucket ENUM('INCOME','SELF_TRANSFER','REFUND','BORROWED','NEED','COMMITTED','WANT','FEE','UNKNOWN')
  NOT NULL COMMENT 'Drives the affordability maths';
