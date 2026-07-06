import pytest

from backend.json_utils import extract_json_or_raise


def test_pure_json():
    assert extract_json_or_raise('{"a": 1, "b": [2, 3]}') == {"a": 1, "b": [2, 3]}


def test_json_with_surrounding_prose():
    text = 'Sure! Here is the result:\n{"companies": [1, 2]}\nLet me know if you need more.'
    assert extract_json_or_raise(text) == {"companies": [1, 2]}


def test_json_with_leading_code_fence():
    text = '```json\n{"emails": []}\n```'
    assert extract_json_or_raise(text) == {"emails": []}


def test_garbage_raises_value_error():
    with pytest.raises(ValueError):
        extract_json_or_raise("there is no json here at all")


def test_malformed_braces_raise_value_error():
    with pytest.raises(ValueError):
        extract_json_or_raise("{ not valid json :: }")
