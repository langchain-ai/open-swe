"""Declarative base shared by every mapped class.

Mapped classes are the domain objects: keyword-only dataclasses whose
``Mapped[...]`` annotations double as the column definitions. Migrations, not
the mappings, are the source of truth for the schema; a mapping must match its
migration column for column.
"""

from datetime import datetime

from sqlalchemy import DateTime, Text, text
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass

NOW = text("clock_timestamp()")


class Base(MappedAsDataclass, DeclarativeBase, kw_only=True):
    type_annotation_map = {datetime: DateTime(timezone=True), str: Text}
