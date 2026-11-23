"""Editorial formatting for generated UI copy and explicit maintenance plans."""


def without_final_period(text):
    """Omit a prose field's final period, preserving internal punctuation and ellipses."""
    if text.endswith('.') and not text.endswith('..') and text[:-1].strip():
        return text[:-1]
    return text
