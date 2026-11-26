"""Shared completeness definition: zero is present; whitespace-only text is missing."""

from sqlalchemy import String, func

WHITESPACE = " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


def is_present(value):
    return value is not None and (not isinstance(value, str) or bool(value.strip(WHITESPACE)))


def present_condition(column):
    if isinstance(column.type, String):
        # Use the same Unicode whitespace set in SQL and Python.
        cleaned = column
        for character in WHITESPACE:
            cleaned = func.replace(cleaned, character, "")
        return column.is_not(None) & (func.length(cleaned) > 0)
    return column.is_not(None)
