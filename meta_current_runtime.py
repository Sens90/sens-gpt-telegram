"""Runtime hook: intercept generic current-meta requests before Gemini prose."""
import re


def is_direct_current_meta(question):
    q = re.sub(r'\s+', ' ', str(question or '').strip().casefold())
    return bool(re.fullmatch(r'(?:il\s+)?meta(?:\s+attuale|\s+di\s+adesso|\s+ora)?[?!.]*', q))


def context_from_question(question):
    q = str(question or '').casefold()
    if 'ladder' in q or 'trofei' in q:
        return 'ladder'
    if 'classificata' in q or 'ranked' in q:
        return 'ranked'
    return 'both'
