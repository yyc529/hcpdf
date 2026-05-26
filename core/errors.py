class Pdf2HtmlError(Exception):
    """Base exception for conversion failures."""


class UnsupportedPdfFeature(Pdf2HtmlError):
    """Raised when a feature cannot be represented by the current MVP renderer."""

