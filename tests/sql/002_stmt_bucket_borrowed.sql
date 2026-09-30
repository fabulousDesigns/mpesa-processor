-- Only safe if no rows use BORROWED yet.
ALTER TABLE stmt_transactions
  MODIFY bucket ENUM('INCOME','SELF_TRANSFER','REFUND','NEED','COMMITTED','WANT','FEE','UNKNOWN')
  NOT NULL COMMENT 'Drives the affordability maths';