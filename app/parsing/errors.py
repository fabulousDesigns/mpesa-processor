class StatementError(Exception):
    """Base for everything that can go wrong turning a PDF into rows."""
    code = "STATEMENT_ERROR"


class WrongCode(StatementError):
    """The code did not open this PDF. Normal during matching, not a crash."""
    code = "WRONG_CODE"


class NotAStatement(StatementError):
    """Opened fine, but it isn't an M-PESA statement we recognise."""
    code = "NOT_A_STATEMENT"


class UnknownLayout(StatementError):
    """Looks like a statement, but the table layout changed. Needs a parser update."""
    code = "UNKNOWN_LAYOUT"
