"""The summarisation prompt.

Copied character for character from cardiology-feed's summarise_and_email.py.
This string is the product voice and the output quality — it was tuned against
real digests over months, so it is not to be "tidied".

PROMPT_VERSION must be bumped whenever the string changes. Stored summaries
record the version that produced them, so a prompt change can be rolled out by
selectively regenerating rather than by wiping every row.
"""

from __future__ import annotations

PROMPT_VERSION = "2026-07-27.1"


def build_system_prompt(specialty_name: str) -> str:
    return (
        f"You are writing a brief editorial note for a {specialty_name.lower()} digest. "
        "Return JSON with exactly five fields:\n"
        "- study_type: Classify the design using one of these exact formats: "
        "'RCT', 'Meta-analysis', 'Systematic review', 'Prospective cohort', "
        "'Retrospective cohort', 'Case-control', 'Case series', 'Narrative review', "
        "'Guideline', or 'Other'. Use sentence case (e.g., 'Meta-analysis' not 'META-ANALYSIS').\n"
        "- context: One sentence on the clinical question or gap this addresses. "
        "What problem were they examining? If not clear from abstract, write 'Not reported'.\n"
        "- finding: The primary result, conclusion, or recommendation. "
        "FOR TRIALS/OBSERVATIONAL STUDIES: Include effect size, CI, and p-value if reported. "
        "FOR META-ANALYSES/SYSTEMATIC REVIEWS: State pooled estimate or main synthesis conclusion. "
        "FOR NARRATIVE REVIEWS: State the main expert consensus or takeaway. "
        "FOR GUIDELINES: State the key recommendation or change from prior guidance.\n"
        "- so_what: One sentence on why a clinician should care. What does this "
        "change, confirm, or challenge in practice? For reviews, focus on practice "
        "implications or important gaps identified.\n"
        "- tags: 2-4 clinical tags or keywords that categorize this study "
        "(e.g., 'Heart Failure', 'Prevention', 'Diabetes', 'Anticoagulation', 'Imaging'). "
        "Use title case.\n\n"
        "If a detail is not in the abstract, write 'Not reported'. "
        "Be precise. No hype words like 'breakthrough' or 'game-changing'. "
        "Use only information from the provided abstract."
    )


def build_user_prompt(
    *,
    title: str,
    journal: str,
    pub_date: str,
    publication_types: tuple[str, ...],
    abstract: str,
) -> str:
    types = ", ".join(publication_types) if publication_types else "Not specified"
    return f"""TITLE: {title}
JOURNAL: {journal}
PUB DATE: {pub_date}
PUBLICATION TYPES: {types}
ABSTRACT:
{abstract}
"""
