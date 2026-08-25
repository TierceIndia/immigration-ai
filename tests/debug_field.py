"""Minimal reproduction: ExtractedRequirement has a field named 'field'"""
from dataclasses import dataclass, field

@dataclass
class ExtractedRequirement:
    field: str = ""  # This is a class attribute named 'field'
    items: list = field(default_factory=list)  # 'field' is now the class attr, not dataclasses.field!
