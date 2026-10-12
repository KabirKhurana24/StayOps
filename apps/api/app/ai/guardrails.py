"""Deterministic guardrails around the model (they hold for every provider).

Before the model: requests the assistant must never handle itself — refunds and
money, cancellations, date changes, other guests' data, internal/system details —
are answered with a fixed message (escalating to the team where a human should
decide). No model call is made for them.

After the model:
* an answer containing a code/number that appears in no tool result of this run is
  treated as a hallucination (door codes, passwords, prices, times) and replaced;
* the guest assistant may never claim to have approved, booked, cancelled,
  refunded or changed anything — it can only pass requests on;
* empty answers fall back to a deterministic message.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InputBlock:
    code: str
    reply: str
    escalate: bool


_INPUT_RULES: list[tuple[str, re.Pattern[str], str, bool]] = [
    (
        "money",
        re.compile(
            r"\b(refund|money back|reimburs\w*|compensat\w*|discount|chargeback|cheaper|pay(?:ment)? back)\b",
            re.I,
        ),
        "I'm not able to handle payments, refunds or discounts, but I've passed your message to the team — they'll reply here.",
        True,
    ),
    (
        "cancellation",
        re.compile(
            r"\bcancel\w*\b.*\b(booking|reservation|stay|trip)\b|\b(booking|reservation|stay)\b.*\bcancel\w*",
            re.I,
        ),
        "I can't cancel or change reservations, but I've let the team know and they'll get back to you here.",
        True,
    ),
    (
        "date_change",
        re.compile(
            r"\b(change|move|shift|extend)\b.*\b(dates?|booking|reservation|stay)\b|\bextra night\b|\bstay (an|one) (extra|more) night",
            re.I,
        ),
        "Changes to your dates need the team to check availability. I've passed your message on — they'll reply here.",
        True,
    ),
    (
        "other_guests",
        re.compile(
            r"\b(other guests?|who(?:'s| is) staying|next guest|previous guest|guest in (?:unit|room|apartment)|"
            r"(?:code|wi-?fi|password) (?:for|of) (?:unit|room|apartment|flat)\s*\w+)\b",
            re.I,
        ),
        "I can only help with your own stay, so I can't share anything about other guests or units.",
        False,
    ),
    (
        "internal",
        re.compile(
            r"\b(system prompt|ignore (?:all |your )?(?:previous|prior) instructions|internal notes?|staff (?:phone|email|names?)|"
            r"owner(?:'s)? (?:details|contact|income|revenue)|your instructions)\b",
            re.I,
        ),
        "I can help with your stay — check-in, the house guide, requests — but I can't share internal information.",
        False,
    ),
]


def screen_guest_input(text: str) -> InputBlock | None:
    for code, pattern, reply, escalate in _INPUT_RULES:
        if pattern.search(text):
            return InputBlock(code=code, reply=reply, escalate=escalate)
    return None


# Tokens carrying a 4+ digit run: codes, PINs, prices — also alphanumeric ("A4827",
# "4827B") and digit groups written with separators ("48-27", "4 8 2 7").
_TOKEN = re.compile(r"[A-Za-z0-9]+")
_DIGIT_GROUP = re.compile(r"\d(?:[ \-]?\d){3,}")
_VERBS = (
    r"(approved|confirmed|booked|cancell?ed|refunded|extended|changed|granted|upgraded|arranged)"
)
_FORBIDDEN_CLAIMS = [
    # "I've approved", "we have booked", "I'll confirm" — first person actions.
    re.compile(rf"\b(?:I|we)(?:'ve| have|'ll| will)?\s+(?:just\s+|already\s+)?{_VERBS}\b", re.I),
    # "your late checkout is confirmed", "has been approved" — passive outcomes.
    re.compile(
        rf"\b(?:is|are|was|were|has been|have been|is now|are now)\s+(?:all\s+)?{_VERBS}\b", re.I
    ),
]


def _normalise(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'")


def _value_tokens(text: str) -> set[str]:
    tokens = {t.lower() for t in _TOKEN.findall(text) if sum(c.isdigit() for c in t) >= 4}
    tokens |= {re.sub(r"[ \-]", "", g) for g in _DIGIT_GROUP.findall(text)}
    return tokens


def _grounded_tokens(text: str) -> set[str]:
    tokens = {t.lower() for t in _TOKEN.findall(text)}
    tokens |= {re.sub(r"[ \-]", "", g) for g in _DIGIT_GROUP.findall(text)}
    return tokens


@dataclass(frozen=True, slots=True)
class OutputCheck:
    ok: bool
    code: str | None = None


def check_answer(
    answer: str,
    tool_payloads: list[dict[str, Any]],
    *,
    guest: bool,
    context_text: str = "",
    failed_actions: bool = False,
) -> OutputCheck:
    """``failed_actions``: a request/escalation tool failed in this run — the answer
    must not be trusted to describe it, so the fallback (with escalation) is used."""
    if not answer.strip():
        return OutputCheck(False, "empty_answer")
    if guest and failed_actions:
        return OutputCheck(False, "action_failed")
    grounded = _grounded_tokens(json.dumps(tool_payloads, ensure_ascii=False) + " " + context_text)
    # Exact token match (not substring): a code can't be "grounded" by a UUID fragment.
    if _value_tokens(answer) - grounded:
        return OutputCheck(False, "ungrounded_value")
    if guest and any(p.search(_normalise(answer)) for p in _FORBIDDEN_CLAIMS):
        return OutputCheck(False, "unauthorized_claim")
    return OutputCheck(True)


GUEST_FALLBACK = "Thanks for your message. I couldn't answer that reliably, so I've passed it to the team — they'll reply here shortly."
COPILOT_FALLBACK = (
    "I couldn't complete that analysis just now. Nothing was changed. Please try again in a moment."
)
