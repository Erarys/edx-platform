"""A graded matching XBlock with a line-based learner interface."""

from __future__ import annotations

import math
import re
import uuid

from django.contrib.staticfiles.storage import staticfiles_storage
from django.utils.translation import gettext, ngettext
from web_fragments.fragment import Fragment
from xblock.completable import XBlockCompletionMode
from xblock.core import XBlock
from xblock.fields import Boolean, Dict, Float, Integer, List, Scope, String

from xmodule.xml_block import XmlMixin


def _(text):
    """Mark field metadata strings for translation without translating at import time."""
    return text


DEFAULT_PAIRS = [
    {
        "id": "pair-1",
        "right_id": "answer-cobalt",
        "left": "Term 1",
        "right": "Definition 1",
    },
    {
        "id": "pair-2",
        "right_id": "answer-amber",
        "left": "Term 2",
        "right": "Definition 2",
    },
    {
        "id": "pair-3",
        "right_id": "answer-jade",
        "left": "Term 3",
        "right": "Definition 3",
    },
]

PAIR_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_PAIRS = 50
MAX_TEXT_LENGTH = 500


def normalize_max_attempts(raw_value):
    """Return a non-negative attempt limit for course and library runtimes."""
    if raw_value is None:
        return 0

    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return 0


def normalize_pairs(raw_pairs):
    """Return safe, unique matching pairs from persisted or submitted data."""
    normalized = []
    used_left_ids = set()
    used_right_ids = set()
    for raw_pair in raw_pairs if isinstance(raw_pairs, list) else []:
        if not isinstance(raw_pair, dict):
            continue

        left = raw_pair.get("left", "")
        right = raw_pair.get("right", "")
        if not isinstance(left, str) or not isinstance(right, str):
            continue

        left = left.strip()[:MAX_TEXT_LENGTH]
        right = right.strip()[:MAX_TEXT_LENGTH]
        if not left or not right:
            continue

        pair_id = raw_pair.get("id", "")
        if (
            not isinstance(pair_id, str)
            or not PAIR_ID_PATTERN.fullmatch(pair_id)
            or pair_id in used_left_ids
        ):
            pair_id = f"pair-{uuid.uuid4().hex[:12]}"

        right_id = raw_pair.get("right_id", "")
        if (
            not isinstance(right_id, str)
            or not PAIR_ID_PATTERN.fullmatch(right_id)
            or right_id in used_right_ids
        ):
            right_id = f"answer-{uuid.uuid4().hex[:12]}"

        used_left_ids.add(pair_id)
        used_right_ids.add(right_id)
        normalized.append(
            {"id": pair_id, "right_id": right_id, "left": left, "right": right}
        )
        if len(normalized) == MAX_PAIRS:
            break

    return normalized


def grade_matches(pairs, matches):
    """Grade a one-to-one mapping and return the correct count and pair ids."""
    expected_matches = {pair["id"]: pair["right_id"] for pair in pairs}
    if not isinstance(matches, dict):
        return 0, []

    correct_ids = sorted(
        pair_id
        for pair_id, right_id in expected_matches.items()
        if matches.get(pair_id) == right_id
    )
    return len(correct_ids), correct_ids


@XBlock.needs("mako")
class MatchingBlock(XBlock, XmlMixin):  # pylint: disable=abstract-method
    """Match the items in two columns by drawing one-to-one connections."""

    has_score = True
    has_author_view = True
    completion_mode = XBlockCompletionMode.COMPLETABLE

    display_name = String(
        display_name=_("Display Name"),
        help=_("The name of this matching problem."),
        default=_("Matching"),
        scope=Scope.settings,
    )
    question = String(
        display_name=_("Question"),
        help=_("Instructions shown above the matching pairs."),
        default=_("Connect each item on the left to its match on the right."),
        scope=Scope.content,
    )
    pairs = List(
        display_name=_("Pairs"),
        help=_("The correct left-to-right pairs."),
        default=DEFAULT_PAIRS,
        scope=Scope.content,
    )
    shuffle_answers = Boolean(
        display_name=_("Shuffle right column"),
        help=_("Display the right column in a different order."),
        default=True,
        scope=Scope.settings,
    )
    max_attempts = Integer(
        display_name=_("Maximum Attempts"),
        help=_("Maximum submissions. Use 0 for unlimited attempts."),
        default=0,
        scope=Scope.settings,
    )
    weight = Float(
        display_name=_("Problem Weight"),
        help=_("Number of points possible for this problem."),
        default=1.0,
        scope=Scope.settings,
    )

    matches = Dict(default={}, scope=Scope.user_state)
    attempts = Integer(default=0, scope=Scope.user_state)
    score = Float(default=0.0, scope=Scope.user_state)

    @property
    def normalized_pairs(self):
        """Return the author-defined pairs in their validated representation."""
        pairs = normalize_pairs(self.pairs)
        return pairs if len(pairs) >= 2 else normalize_pairs(DEFAULT_PAIRS)

    @property
    def is_locked(self):
        """Return whether the learner can no longer change the answer."""
        max_attempts = normalize_max_attempts(self.max_attempts)
        return self.score >= 1.0 or (
            max_attempts > 0 and self.attempts >= max_attempts
        )

    def max_score(self):
        """Return the maximum grade published by this block."""
        return max(0.0, self.weight)

    def get_icon_class(self):
        """Render this XBlock with the standard problem icon in Studio and LMS."""
        return "problem"

    def _valid_saved_matches(self, pairs):
        left_ids = {pair["id"] for pair in pairs}
        right_ids = {pair["right_id"] for pair in pairs}
        if not isinstance(self.matches, dict):
            return {}
        return {
            left_id: right_id
            for left_id, right_id in self.matches.items()
            if left_id in left_ids and right_id in right_ids
        }

    def _right_column(self, pairs):
        right_pairs = list(pairs)
        if self.shuffle_answers and len(right_pairs) > 1:
            # A stable rotation prevents the answers from lining up while keeping
            # the interface unchanged when the learner refreshes the page.
            right_pairs = right_pairs[1:] + right_pairs[:1]
        return right_pairs

    @XBlock.supports("multi_device")
    def student_view(self, _context=None):
        """Render the learner-facing matching interaction."""
        pairs = self.normalized_pairs
        maximum = self.max_score()
        max_attempts = normalize_max_attempts(self.max_attempts)
        saved_matches = self._valid_saved_matches(pairs)
        _, correct_pair_ids = (
            grade_matches(pairs, saved_matches) if self.attempts else (0, [])
        )

        fragment = Fragment(
            self.runtime.service(self, "mako").render_lms_template(
                "matching_block.html",
                {
                    "display_name": self.display_name,
                    "question": self.question,
                    "pairs": pairs,
                    "right_pairs": self._right_column(pairs),
                    "attempts": self.attempts,
                    "max_attempts": max_attempts,
                    "earned_score": self.score * maximum,
                    "max_score": maximum,
                },
            )
        )
        fragment.add_css_url(staticfiles_storage.url("common/css/matching_block.css"))
        fragment.add_javascript_url(
            staticfiles_storage.url("common/js/matching_block.js")
        )
        fragment.initialize_js(
            "MatchingBlock",
            {
                "matches": saved_matches,
                "correctPairIds": correct_pair_ids,
                "locked": self.is_locked,
                "total": len(pairs),
                "attempts": self.attempts,
                "maxAttempts": max_attempts,
                "score": self.score * maximum,
                "maxScore": maximum,
                "messages": {
                    "selectLeft": gettext(
                        "Select an item on the left, then select its match on the right."
                    ),
                    "connected": gettext("Connected {left} to {right}."),
                    "completeAll": gettext(
                        "Connect every item before checking your answer."
                    ),
                    "correct": gettext("Correct! All pairs match."),
                    "incorrect": gettext(
                        "{correct} of {total} pairs are correct. Try again."
                    ),
                    "locked": gettext("No attempts remain."),
                    "requestFailed": gettext(
                        "Your answer could not be saved. Please try again."
                    ),
                    "progress": gettext("{earned}/{maximum} points"),
                    "attempts": gettext("Attempts used: {attempts}"),
                    "attemptsLimited": ngettext(
                        "You have used {attempts} of {max_attempts} attempt",
                        "You have used {attempts} of {max_attempts} attempts",
                        max_attempts,
                    ),
                },
            },
        )
        return fragment

    def author_view(self, context=None):
        """Use the real learner interaction as the Studio preview."""
        return self.student_view(context)

    @XBlock.json_handler
    def submit(self, data, suffix=""):  # pylint: disable=unused-argument
        """Validate, persist, grade, and publish one learner submission."""
        if self.is_locked:
            return {"result": "error", "message": gettext("This problem is locked.")}

        pairs = self.normalized_pairs
        left_ids = {pair["id"] for pair in pairs}
        right_ids = {pair["right_id"] for pair in pairs}
        submitted_matches = data.get("matches") if isinstance(data, dict) else None
        if not isinstance(submitted_matches, dict):
            return {"result": "error", "message": gettext("Invalid matching answer.")}

        matches = {
            left_id: right_id
            for left_id, right_id in submitted_matches.items()
            if left_id in left_ids and right_id in right_ids
        }
        if set(matches) != left_ids or set(matches.values()) != right_ids:
            return {
                "result": "error",
                "message": gettext(
                    "Connect every item exactly once before checking your answer."
                ),
            }

        correct_count, correct_pair_ids = grade_matches(pairs, matches)
        self.matches = matches
        self.attempts += 1
        self.score = correct_count / len(pairs)

        maximum = self.max_score()
        self.runtime.publish(
            self,
            "grade",
            {"value": self.score * maximum, "max_value": maximum},
        )
        self.runtime.publish(self, "completion", {"completion": 1.0})

        return {
            "result": "success",
            "status": "correct" if correct_count == len(pairs) else "incorrect",
            "correct": correct_count,
            "total": len(pairs),
            "correctPairIds": correct_pair_ids,
            "attempts": self.attempts,
            "locked": self.is_locked,
            "score": self.score * maximum,
            "maxScore": maximum,
        }

    def studio_view(self, _context=None):
        """Render the purpose-built Studio editor for matching pairs."""
        fragment = Fragment(
            self.runtime.service(self, "mako").render_cms_template(
                "widgets/matching_block_editor.html",
                {
                    "display_name": self.display_name,
                    "question": self.question,
                    "pairs": self.normalized_pairs,
                    "shuffle_answers": self.shuffle_answers,
                    "max_attempts": normalize_max_attempts(self.max_attempts),
                    "weight": self.weight,
                },
            )
        )
        fragment.add_css_url(staticfiles_storage.url("common/css/matching_block.css"))
        fragment.add_javascript_url(
            staticfiles_storage.url("common/js/matching_block_studio.js")
        )
        fragment.initialize_js(
            "MatchingBlockStudio",
            {
                "messages": {
                    "removePair": gettext("Remove pair"),
                    "leftItem": gettext("Left item"),
                    "rightMatch": gettext("Right match"),
                    "minimumPairs": gettext(
                        "A matching problem needs at least two pairs."
                    ),
                    "completeFields": gettext(
                        "Complete the display name, question, and both sides of every pair."
                    ),
                    "saveFailed": gettext(
                        "The matching problem could not be saved. Please try again."
                    ),
                    "errorTitle": gettext("Error saving changes"),
                    "points": gettext("{weight} points"),
                    "attemptsSummary": gettext("{attempts} attempts"),
                    "unlimitedAttempts": gettext("unlimited attempts"),
                    "rightColumnShuffled": gettext("Right column shuffled"),
                    "fixedAnswerOrder": gettext("Fixed answer order"),
                }
            },
        )
        return fragment

    @XBlock.json_handler
    def studio_submit(self, data, suffix=""):  # pylint: disable=unused-argument
        """Validate and persist author settings."""
        if not isinstance(data, dict):
            return {"result": "error", "message": gettext("Invalid settings payload.")}

        display_name = data.get("display_name", "")
        question = data.get("question", "")
        if not isinstance(display_name, str) or not display_name.strip():
            return {"result": "error", "message": gettext("Display name is required.")}
        if not isinstance(question, str) or not question.strip():
            return {"result": "error", "message": gettext("Question is required.")}

        raw_pairs = data.get("pairs")
        pairs = normalize_pairs(raw_pairs)
        if len(pairs) < 2:
            return {
                "result": "error",
                "message": gettext("Add at least two complete pairs."),
            }
        if len(pairs) != len(raw_pairs if isinstance(raw_pairs, list) else []):
            return {
                "result": "error",
                "message": gettext("Every pair must have text on both sides."),
            }

        left_values = [pair["left"].casefold() for pair in pairs]
        right_values = [pair["right"].casefold() for pair in pairs]
        if len(set(left_values)) != len(left_values) or len(set(right_values)) != len(
            right_values
        ):
            return {
                "result": "error",
                "message": gettext("Pair values must be unique within each column."),
            }

        try:
            max_attempts = int(data.get("max_attempts", 0))
            weight = float(data.get("weight", 1.0))
        except (TypeError, ValueError):
            return {
                "result": "error",
                "message": gettext("Attempts and weight must be numbers."),
            }
        if max_attempts < 0:
            return {
                "result": "error",
                "message": gettext("Maximum attempts cannot be negative."),
            }
        if not math.isfinite(weight) or weight < 0:
            return {
                "result": "error",
                "message": gettext(
                    "Problem weight must be a non-negative finite number."
                ),
            }

        self.display_name = display_name.strip()[:MAX_TEXT_LENGTH]
        self.question = question.strip()[:MAX_TEXT_LENGTH]
        self.pairs = pairs
        self.shuffle_answers = bool(data.get("shuffle_answers", False))
        self.max_attempts = max_attempts
        self.weight = weight
        return {"result": "success"}
