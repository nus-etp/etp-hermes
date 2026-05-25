"""Slug derivation rule from prompts/synthesis.md."""

from __future__ import annotations

import pytest

from tests._slug import slug


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Carousell", "carousell"),
        ("Horizon Quantum Computing", "horizon-quantum-computing"),
        ("NEU Battery Materials", "neu-battery-materials"),
        ("CBE Eco-Solutions", "cbe-eco-solutions"),
        ("Green COP", "green-cop"),
        ("polybee", "polybee"),
        ("BEEX", "beex"),
        ("Foo  Bar___Baz", "foo-bar-baz"),
        ("  -- Acme, Inc. -- ", "acme-inc"),
        ("---", ""),
    ],
)
def test_slug_examples(name: str, expected: str) -> None:
    assert slug(name) == expected
