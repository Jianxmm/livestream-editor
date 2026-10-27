"""Shared validation for manually entered review dates; does not prove review occurred."""
from datetime import date
import re


def valid_review_date(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value.strip()):
        return False
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        return False
    return True
