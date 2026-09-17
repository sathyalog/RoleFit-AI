"""Streamlit-free, side-effect-free resume/JD screening logic.

Shared by main.py (the Streamlit app) and evaluation.py (the offline eval
harness) so the two can never silently drift apart. Nothing here touches
Streamlit, Pinecone, or the filesystem.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langsmith import traceable

from core.schemas import ScreeningModel

DEFAULT_SCREENING_MODEL_NAME = "claude-haiku-4-5-20251001"


class ScreeningState(TypedDict, total=False):
    company_name: Optional[str]
    candidate_name: Optional[str]
    job_title: Optional[str]
    candidate_experience: Optional[float]
    experience_required: Optional[float]
    skill_match: Optional[float]
    required_skills: List[str]
    candidate_skills: List[str]
    matched_skills: List[str]
    resume_text: Optional[str]
    job_description: Optional[str]
    github_handle: Optional[str]
    github_mcp_output: Optional[str]
    pii_scrubbed: bool
    rejection_feedback: str
    critique: str
    reflection_count: int


def build_structured_model(llm: Optional[BaseChatModel] = None):
    """Wrap (or lazily create) a ChatAnthropic client with ScreeningModel
    structured output. main.py passes its existing shared `llm`; evaluation.py
    calls this with no args to build its own client — keeping model/temperature/
    max_tokens config defined in exactly one place."""
    if llm is None:
        llm = ChatAnthropic(model=DEFAULT_SCREENING_MODEL_NAME, temperature=0, max_tokens=2500)
    return llm.with_structured_output(ScreeningModel)


def build_extraction_prompt(resume_text: str, job_description: str) -> str:
    return f"""
    You are an expert technical recruiter parsing a Candidate Resume and a Job Description.
    Extract candidate_name, company_name, job_title, candidate_experience, experience_required, and detected_roles.
    Extract required_skills and candidate_skills exhaustively. Normalize common tech names.

    Candidate Resume: {resume_text}
    Job Description: {job_description}
    """


@traceable(name="extract_screening_output")
def extract_screening_output(resume_text: str, job_description: str, structured_model) -> ScreeningModel:
    """The one LLM call in this module. No Streamlit, no session state."""
    prompt = build_extraction_prompt(resume_text, job_description)
    return structured_model.invoke(prompt)


def compute_skill_match(required_skills: List[str], candidate_skills: List[str]) -> Dict[str, Any]:
    """Pure skill-overlap scoring: the exact lowercase-intersection rule the
    app uses. No LLM call. Returns {"matched_skills": [...], "skill_match": float}."""
    req_skills = required_skills or []
    cand_skills = candidate_skills or []
    req_set_lower = {s.strip().lower() for s in req_skills if s.strip()}
    cand_set_lower = {s.strip().lower() for s in cand_skills if s.strip()}
    matched_set_lower = req_set_lower.intersection(cand_set_lower)
    exact_matched = [s for s in req_skills if s.strip().lower() in matched_set_lower]
    exact_score = len(matched_set_lower) / len(req_set_lower) if req_set_lower else 0.0
    return {"matched_skills": exact_matched, "skill_match": exact_score}


@traceable(name="check_criteria")
def CheckCriteria(state: ScreeningState) -> Literal["ShortList", "Reject"]:
    skill_match = state.get("skill_match", 0.0)
    candidate_exp = state.get("candidate_experience", 0.0)
    exp_required = state.get("experience_required", 0.0)
    return "ShortList" if (skill_match >= 0.50 and candidate_exp >= exp_required) else "Reject"
