import re
from difflib import SequenceMatcher

from sqlalchemy import func, or_
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement


class FuzzySearch:
    def __init__(self, query: str) -> None:
        self.query = query.strip().lower()

    def matches(self, value: str) -> bool:
        name = value.lower()
        if not self.query or self.query in name:
            return True
        if any(char.isdigit() for char in self.query):
            return False
        return SequenceMatcher(None, self.query, name).ratio() * 60 >= 25 or (
            len(self.query) >= 3
            and any(
                SequenceMatcher(None, self.query, word).ratio() >= 0.6
                for word in re.findall(r"[\w-]+", name)
            )
        )

    def condition(self, column: InstrumentedAttribute[str]) -> ColumnElement[bool]:
        exact = column.icontains(self.query, autoescape=True)
        if (
            len(self.query) < 3
            or any(char.isdigit() for char in self.query)
            or re.search(r"[^\w\s-]", self.query)
        ):
            return exact
        return or_(exact, func.public.word_similarity(self.query, func.lower(column)) >= 0.5)
