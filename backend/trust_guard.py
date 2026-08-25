"""
Numeric consistency guard — a deterministic (non-LLM) trust layer.

Directly targets the assessment's "Problem 2: Trust and Reliability":
a confidently wrong number (a fee, a credit amount, an SLA time) is the
single most damaging failure mode for a financial-services support tool,
because it's the kind of error a reader won't catch just by re-reading
the prose — it looks specific and therefore trustworthy.

This scans the agent's final answer for INR currency figures and flags
any that don't appear in the text of whatever sources were actually cited
for that turn. It cannot prove the number is *right* (that still requires
correct reasoning), but it catches the cheapest and most dangerous
failure: a number invented or misremembered rather than read off a source.

Deliberately simple and explainable — a regex-based check, not another
model call, so it can't itself hallucinate and adds ~zero latency/cost.
"""

import re

CURRENCY_PATTERN = re.compile(r"(?:₹|INR|Rs\.?)\s?([\d,]+(?:\.\d+)?)")


def extract_currency_figures(text: str) -> set[str]:
    """Returns normalized (comma-stripped) currency figures found in text."""
    return {m.replace(",", "") for m in CURRENCY_PATTERN.findall(text)}


def check_numeric_grounding(answer_text: str, cited_source_texts: list[str]) -> dict:
    """Returns which currency figures in the answer are/aren't traceable to
    the cited sources' actual text. An empty 'unverified' list is a good
    sign; a non-empty one deserves a second look before trusting the answer."""
    answer_figures = extract_currency_figures(answer_text)
    if not answer_figures:
        return {"figures_in_answer": [], "unverified": [], "all_grounded": True}

    source_figures = set()
    for src in cited_source_texts:
        source_figures |= extract_currency_figures(src)

    unverified = sorted(answer_figures - source_figures)
    return {
        "figures_in_answer": sorted(answer_figures),
        "unverified": unverified,
        "all_grounded": len(unverified) == 0,
    }


if __name__ == "__main__":
    # a grounded case
    src = ["LumenWorks receives a fixed INR 300 service credit."]
    r = check_numeric_grounding("LumenWorks is owed ₹300.", src)
    print("grounded case:", r)

    # a hallucinated case — agent invents a number not in any cited source
    r2 = check_numeric_grounding("LumenWorks is owed ₹750.", src)
    print("hallucinated case:", r2)
