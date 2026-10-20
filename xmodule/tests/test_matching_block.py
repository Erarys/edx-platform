"""Tests for the matching XBlock's validation and grading helpers."""

from xmodule.matching_block import (
    MAX_PAIRS,
    grade_matches,
    normalize_max_attempts,
    normalize_pairs,
)


def test_normalize_max_attempts_handles_nullable_library_setting():
    assert normalize_max_attempts(None) == 0
    assert normalize_max_attempts(2) == 2
    assert normalize_max_attempts(-1) == 0
    assert normalize_max_attempts("invalid") == 0


def test_normalize_pairs_drops_incomplete_and_repairs_duplicate_ids():
    pairs = normalize_pairs(
        [
            {"id": "same", "right_id": "one", "left": " First ", "right": " One "},
            {"id": "same", "right_id": "one", "left": "Second", "right": "Two"},
            {"id": "incomplete", "right_id": "three", "left": "", "right": "Three"},
            "not-a-pair",
        ]
    )

    assert [(pair["left"], pair["right"]) for pair in pairs] == [
        ("First", "One"),
        ("Second", "Two"),
    ]
    assert pairs[0]["id"] == "same"
    assert pairs[1]["id"] != "same"
    assert pairs[0]["right_id"] == "one"
    assert pairs[1]["right_id"] != "one"


def test_normalize_pairs_limits_problem_size():
    pairs = [
        {
            "id": f"pair-{index}",
            "right_id": f"answer-{index}",
            "left": f"Left {index}",
            "right": f"Right {index}",
        }
        for index in range(MAX_PAIRS + 5)
    ]

    assert len(normalize_pairs(pairs)) == MAX_PAIRS


def test_grade_matches_counts_only_correct_pair_ids():
    pairs = normalize_pairs(
        [
            {"id": "a", "right_id": "three", "left": "A", "right": "One"},
            {"id": "b", "right_id": "one", "left": "B", "right": "Two"},
            {"id": "c", "right_id": "two", "left": "C", "right": "Three"},
        ]
    )

    correct_count, correct_ids = grade_matches(
        pairs, {"a": "three", "b": "two", "c": "one", "extra": "extra"}
    )

    assert correct_count == 1
    assert correct_ids == ["a"]
