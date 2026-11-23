"""Normalize prose and code identifiers without changing displayed source."""

import re
from collections.abc import Iterator
from functools import lru_cache
from unicodedata import normalize

LEXEME = re.compile(r"\w+(?:[./\\-]\w+)*")
SEPARATOR = re.compile(r"[._/-]+")
QUERY_STOP_WORDS = frozenset(
    "a about an and are as at be by can could did do does for from had has have how "
    "i in into is it its me of on or our should so than that the their them there "
    "these they this those to was we were what when where which who why will with would you your".split()
)


def normalized_exact(text: str) -> str:
    return normalize("NFKC", text).replace("\\", "/").casefold()


def _split_case_and_digits(identifier: str) -> Iterator[str]:
    start = 0
    for index in range(1, len(identifier)):
        previous, current = identifier[index - 1], identifier[index]
        if (
            previous.isdecimal() != current.isdecimal()
            or previous.islower() and current.isupper()
            or previous.isupper() and current.isupper()
            and index + 1 < len(identifier) and identifier[index + 1].islower()
        ):
            yield identifier[start:index].casefold()
            start = index
    if identifier:
        yield identifier[start:].casefold()


@lru_cache(maxsize=4096)
def _variants(word: str) -> tuple[str, ...]:
    variants = [word.casefold()]
    variants.extend(piece.casefold() for piece in word.split("/") if piece)
    for piece in SEPARATOR.split(word):
        if piece:
            variants.append(piece.casefold())
            variants.extend(_split_case_and_digits(piece))
    return tuple(dict.fromkeys(variants))


def tokenize(text: str) -> Iterator[str]:
    """Keep exact forms and split aliases, counting an alias once per occurrence."""
    for match in LEXEME.finditer(normalize("NFKC", text)):
        word = match.group().replace("\\", "/")
        if any(character.isalnum() for character in word):
            yield from _variants(word)


def query_terms(query: str) -> tuple[str, ...]:
    return tuple(sorted(set(tokenize(query)) - QUERY_STOP_WORDS))
