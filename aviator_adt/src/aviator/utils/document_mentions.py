"""Utility for parsing document mentions in user messages.

Supports format: /documentname (ID: 123) or /documentname(ID: 123)

Examples:
    - /report.pdf (ID: 321-431)
    - /analysis.docx(ID: 12345)

"""

import logging
import re

from aviator.models import WhereClauseReferenceModel

logger = logging.getLogger(__name__)


def extract_document_mentions(message: str) -> list[WhereClauseReferenceModel]:
    """Extract document mentions from a message and return document references.

    Parses patterns like:
    - /filename.ext (ID: document_id)
    - /filename.ext(ID: document_id)

    Args:
        message: The user message potentially containing document mentions

    Returns:
        list[WhereClauseReferenceModel]: List of document references extracted from the message

    Examples:
        >>> msg = "Can you summarize /report.pdf (ID: 123-456)?"
        >>> refs = extract_document_mentions(msg)
        >>> refs[0].document_id
        '123-456'

    """

    # Pattern: /filename (ID: docid) or /filename(ID: docid)
    # Captures filename and document ID
    pattern = r"/([^\s()]+)\s*\(ID:\s*([^\)]+)\)"

    matches = list(re.finditer(pattern, message, re.IGNORECASE))

    document_refs = []

    # Extract all matches
    for match in matches:
        doc_id = match.group(2).strip()
        document_refs.append(WhereClauseReferenceModel(document_id=doc_id))
    logger.info("Extracted %d document mention(s) from message", len(document_refs))

    return document_refs
