# linkedin/screening/gate1.py
"""
Gate 1: Structured eligibility checks + LLM-based hard filter verification.
All check functions, logic, and scoring rules are identical to the original architecture.
"""

import json
import re
import logging
from typing import Optional, List

from linkedin.screening.models import (
    GateStatus, ApplicationFormAnswers
)
from linkedin.templates.renderer import call_llm

logger = logging.getLogger(__name__)


# ─── City Normalization (exact copy) ─────────────────────────────────

CITY_NORMALIZE = {
    "bangalore": "bengaluru", "bengaluru": "bengaluru",
    "mumbai": "mumbai",       "bombay": "mumbai",
    "chennai": "chennai",     "madras": "chennai",
    "delhi": "ncr",           "new delhi": "ncr",
    "gurugram": "ncr",        "gurgaon": "ncr",
    "noida": "ncr",           "faridabad": "ncr",  "ghaziabad": "ncr",
}

def normalize_city(city: str) -> str:
    c = city.lower().strip()
    for suffix in [" division", " region", " area", " metropolitan", " city"]:
        if c.endswith(suffix):
            c = c[:-len(suffix)].strip()
    return CITY_NORMALIZE.get(c, c)


# ─── Gate 1 Check Functions (exact copy) ─────────────────────────────

def check_location(candidate_city: str, open_to_relocation: bool,
                   role_location: list, role_remote: bool) -> dict:
    if role_remote:
        return {"status": GateStatus.PASS, "reason": "Role is remote — location not a constraint"}
    if not candidate_city:
        return {"status": GateStatus.UNCLEAR, "reason": "Candidate did not provide current city"}

    norm_candidate   = normalize_city(candidate_city)
    norm_role_cities = [normalize_city(c) for c in role_location]

    if norm_candidate in norm_role_cities:
        return {"status": GateStatus.PASS, "reason": f"Candidate city ({candidate_city}) matches role location"}
    if open_to_relocation:
        return {"status": GateStatus.PASS, "reason": f"City mismatch but candidate is open to relocation"}
    return {"status": GateStatus.FAIL,
            "reason": f"Candidate city ({candidate_city}) doesn't match {', '.join(role_location)} and not open to relocate"}


def check_experience(candidate_years: float, exp_range: dict) -> dict:
    min_exp = exp_range.get("min", 0)
    max_exp = exp_range.get("max", 99)
    buffer  = exp_range.get("buffer", 1)
    lower, upper, over = min_exp - buffer, max_exp + buffer, max_exp + buffer + 2

    if lower <= candidate_years <= upper:
        return {"status": GateStatus.PASS, "reason": f"{candidate_years}y within range ({lower}–{upper})"}
    if candidate_years < lower:
        return {"status": GateStatus.FAIL, "reason": f"{candidate_years}y below minimum threshold ({lower}y)"}
    if upper < candidate_years <= over:
        return {"status": GateStatus.UNCLEAR, "reason": f"{candidate_years}y borderline overqualified — recruiter review needed"}
    return {"status": GateStatus.FAIL, "reason": f"{candidate_years}y significantly overqualified (>{over}y)"}


def check_work_mode(role_work_mode: str, candidate_preference: str) -> dict:
    role = role_work_mode.lower()
    pref = candidate_preference.lower()
    if role in ("remote", "hybrid"):
        return {"status": GateStatus.PASS, "reason": f"Role is {role} — all preferences accepted"}

    if pref in ("in_office", "flexible"):
        return {"status": GateStatus.PASS, "reason": "Candidate comfortable with in-office"}
    if pref == "hybrid":
        return {"status": GateStatus.UNCLEAR, "reason": "In-office role, hybrid candidate — recruiter should confirm"}
    return {"status": GateStatus.FAIL, "reason": "Role is in-office, candidate wants remote only"}


def check_languages(candidate_languages: list, required_languages: list) -> dict:
    if not candidate_languages:
        return {"status": GateStatus.UNCLEAR, "reason": "Candidate did not specify languages"}
    candidate_lower = [l.lower() for l in candidate_languages]
    missing = [l for l in required_languages if l.lower() not in candidate_lower]
    if not missing:
        return {"status": GateStatus.PASS, "reason": "Candidate speaks all required languages"}
    return {"status": GateStatus.FAIL, "reason": f"Missing required language(s): {', '.join(missing)}"}


def check_work_authorization(role_location: list, candidate_auth: Optional[str]) -> dict:
    return {"status": GateStatus.PASS, "reason": "India-based role — work authorization auto-passed"}


# ─── Gate 1 Step 1: Structured Checks (exact copy) ──────────────────

def run_gate1_structured(application_form: ApplicationFormAnswers, role_profile: dict) -> dict:
    stage1    = role_profile.get("stage1", {})
    
    # Support both flat and nested experience config
    exp_range = stage1.get("experience_range", {})
    if not exp_range:
        exp_range = {
            "min": stage1.get("min_experience", 0),
            "max": stage1.get("max_experience", 10),
            "buffer": 1
        }

    checks = {
        "location":           check_location(application_form.current_city,
                                             application_form.open_to_relocation,
                                             stage1.get("location", []),
                                             stage1.get("remote", False)),
        "experience":         check_experience(application_form.years_experience, exp_range),
        "work_mode":          check_work_mode(stage1.get("work_mode", "in_office"),
                                              application_form.work_mode_preference),
        "languages":          check_languages(application_form.languages,
                                              stage1.get("languages", [])),
        "work_authorization": check_work_authorization(stage1.get("location", []),
                                                       application_form.work_authorization),
    }

    statuses = [v["status"] for v in checks.values()]
    if GateStatus.FAIL in statuses:
        overall = GateStatus.FAIL
    elif GateStatus.UNCLEAR in statuses:
        overall = GateStatus.UNCLEAR
    else:
        overall = GateStatus.PASS

    return {
        "checks": checks,
        "overall_status": overall,
        "unclear_items": [k for k, v in checks.items() if v["status"] == GateStatus.UNCLEAR],
        "failed_items":  [k for k, v in checks.items() if v["status"] == GateStatus.FAIL],
    }


# ─── Gate 1 Step 2: Hard Filters (LLM-based) ────────────────────────

def run_gate1_hard_filters(resume_text: str, hard_filters: list) -> dict:
    """
    Uses LLM to check resume against hard knockout filters.
    Adapted to use synchronous call_llm() instead of async aiohttp.
    """
    if not hard_filters:
        return {"skipped": True, "reason": "No hard filters set",
                "overall_status": GateStatus.PASS, "filter_results": [], "unclear_items": []}

    filters_formatted = "\n".join(
        [f"Requirement {i+1}: {f}" for i, f in enumerate(hard_filters)]
    )
    prompt = f"""Here is a candidate's resume. For each requirement below, determine if the resume provides clear evidence the candidate meets it.

Return ONLY a valid JSON array. Each element must have:
- "requirement": the requirement text
- "result": "YES", "NO", or "UNCLEAR"
- "reason": one evidence-based sentence

Requirements:
{filters_formatted}

Resume:
{resume_text}

Return ONLY the JSON array. No other text."""

    try:
        raw = call_llm(prompt)

        try:
            filter_results = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            filter_results = json.loads(match.group()) if match else []

        has_fail    = any(r["result"] == "NO"      for r in filter_results)
        has_unclear = any(r["result"] == "UNCLEAR" for r in filter_results)

        overall = GateStatus.FAIL if has_fail else (GateStatus.UNCLEAR if has_unclear else GateStatus.PASS)

        return {
            "skipped": False,
            "overall_status": overall,
            "filter_results": filter_results,
            "unclear_items": [r["requirement"] for r in filter_results if r["result"] == "UNCLEAR"],
            "failed_items":  [r["requirement"] for r in filter_results if r["result"] == "NO"],
        }
    except Exception as e:
        logger.error(f"Gate 1 Hard Filter LLM call failed: {e}")
        return {
            "skipped": False,
            "overall_status": GateStatus.UNCLEAR,
            "filter_results": [],
            "unclear_items": ["LLM call failed — manual review needed"],
            "failed_items": [],
            "error": str(e)
        }
