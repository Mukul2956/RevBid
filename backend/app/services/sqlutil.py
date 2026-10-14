def escape_like(term: str) -> str:
    """Escape LIKE wildcards so user input is matched literally (use with ``escape="\\\\"``)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
