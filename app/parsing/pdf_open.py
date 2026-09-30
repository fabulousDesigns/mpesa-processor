"""Decrypt a Safaricom statement with the customer's 6-digit code.

Everything happens in memory. The decrypted bytes are returned to the caller, never
written to local disk, so a crash can't leave a readable statement lying around.
"""
import io

import pikepdf

from app.parsing.errors import NotAStatement, WrongCode


def is_encrypted(pdf_bytes: bytes) -> bool:
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)):
            return False
    except pikepdf.PasswordError:
        return True


def try_open(pdf_bytes: bytes, code: str) -> bytes:
    """Return decrypted PDF bytes, or raise WrongCode. Cheap enough to call per candidate."""
    code = (code or "").strip()
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes), password=code) as pdf:
            if not pdf.is_encrypted:
                # Safaricom always encrypts. An unencrypted "statement" arriving by email is
                # not what Safaricom sends, so we treat it as suspicious rather than a free pass.
                raise NotAStatement("PDF was not password protected")
            out = io.BytesIO()
            pdf.save(out)  # saving without encryption= strips the password
            return out.getvalue()
    except pikepdf.PasswordError as e:
        raise WrongCode("code did not open the PDF") from e
    except pikepdf.PdfError as e:
        raise NotAStatement(f"not a readable PDF: {e}") from e


def producer_of(pdf_bytes: bytes, code: str | None = None) -> str | None:
    """PDF Producer metadata. Genuine Safaricom files have a consistent producer; an
    edited statement often shows an editor's name instead. Used as a tamper signal."""
    with pikepdf.open(io.BytesIO(pdf_bytes), password=code or "") as pdf:
        return str(pdf.docinfo.get("/Producer")) if "/Producer" in pdf.docinfo else None
