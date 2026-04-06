# linkedin/screening/engine.py
"""
Screening Engine — Orchestrates Gate 1 + Gate 2 for enriched LinkedIn profiles.

Key responsibilities:
1. Extract candidate attributes from enriched LinkedIn profile data
   (city, experience years, languages, etc.) to auto-fill ApplicationFormAnswers
2. Synthesize a "resume text" from LinkedIn profile for LLM evaluation
3. Manage role profile storage (JSON files)
4. Store and retrieve screening results
"""

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from linkedin.screening.models import (
    ApplicationFormAnswers, WorkModePreference, GateStatus, ScreeningStatus,
    RoleProfile, Stage1RoleBasics, Stage2RoleMotion, Stage3FiltersAndJD,
    Compensation
)
from linkedin.screening.gate1 import run_gate1_structured, run_gate1_hard_filters
from linkedin.screening.gate2 import run_gate2_scoring

logger = logging.getLogger(__name__)

# Paths
from linkedin.conf import ASSETS_DIR

ROLE_PROFILES_DIR = ASSETS_DIR / "role_profiles"
SCREENING_RESULTS_DIR = ASSETS_DIR / "screening_results"

ROLE_PROFILES_DIR.mkdir(exist_ok=True)
SCREENING_RESULTS_DIR.mkdir(exist_ok=True)


# ─── Profile → Resume Text Synthesis ─────────────────────────────────

def synthesize_resume_text(profile_data: dict) -> str:
    """
    Convert an enriched LinkedIn profile JSON into a structured resume-like
    text block that the Gate 2 LLM can evaluate.
    """
    parts = []

    # Header
    name = profile_data.get("full_name", "Unknown Candidate")
    headline = profile_data.get("headline", "")
    location = profile_data.get("location_name", "")
    parts.append(f"# {name}")
    if headline:
        parts.append(f"**Headline:** {headline}")
    if location:
        parts.append(f"**Location:** {location}")

    # About / Summary
    about = profile_data.get("summary") or profile_data.get("about", "")
    if about:
        parts.append(f"\n## About\n{about}")

    # Experience / Positions
    positions = profile_data.get("positions", [])
    if positions:
        parts.append("\n## Experience")
        for pos in positions:
            title = pos.get("title", "Unknown Role")
            company = pos.get("company_name", "Unknown Company")
            description = pos.get("description", "")

            # Date range
            dr = pos.get("date_range", {})
            start = dr.get("start", {})
            end = dr.get("end", {})
            start_str = f"{start.get('month', '?')}/{start.get('year', '?')}" if start else "?"
            end_str = f"{end.get('month', '?')}/{end.get('year', '?')}" if (end and end.get('year')) else "Present"
            date_str = f"{start_str} – {end_str}"

            parts.append(f"\n### {title} @ {company}")
            parts.append(f"*{date_str}*")

            # Company details
            comp_details = pos.get("company_details", {})
            if comp_details:
                industry = comp_details.get("industry", "")
                size = comp_details.get("employee_count", "")
                comp_desc = comp_details.get("description", "")
                if industry:
                    parts.append(f"Industry: {industry}")
                if size:
                    parts.append(f"Company Size: {size}")
                if comp_desc:
                    parts.append(f"Company Description: {comp_desc}")

            if description:
                parts.append(f"\n{description}")

    # Education
    educations = profile_data.get("educations", [])
    if educations:
        parts.append("\n## Education")
        for edu in educations:
            school = edu.get("school_name", "")
            degree = edu.get("degree_name", "")
            field = edu.get("field_of_study", "")
            dr = edu.get("date_range", {})
            s_year = (dr.get("start") or {}).get("year", "?")
            e_year = (dr.get("end") or {}).get("year", "?")
            line = f"- **{degree}** in {field}" if field else f"- **{degree}**"
            if school:
                line += f" — {school}"
            line += f" ({s_year}–{e_year})"
            parts.append(line)

    # Skills
    skills = profile_data.get("skills", [])
    if skills:
        skill_names = []
        for s in skills:
            if isinstance(s, dict):
                skill_names.append(s.get("name", str(s)))
            else:
                skill_names.append(str(s))
        parts.append(f"\n## Skills\n{', '.join(skill_names)}")

    # Certifications
    certs = profile_data.get("certifications", [])
    if certs:
        parts.append("\n## Certifications")
        for c in certs:
            if isinstance(c, dict):
                parts.append(f"- {c.get('name', str(c))}")
            else:
                parts.append(f"- {c}")

    # Languages
    languages = profile_data.get("languages", [])
    if languages:
        lang_names = []
        for l in languages:
            if isinstance(l, dict):
                lang_names.append(l.get("name", str(l)))
            else:
                lang_names.append(str(l))
        parts.append(f"\n## Languages\n{', '.join(lang_names)}")

    return "\n".join(parts)


# ─── Profile → ApplicationFormAnswers (auto-extraction) ──────────────

def extract_candidate_attributes(profile_data: dict) -> ApplicationFormAnswers:
    """
    Auto-extract candidate attributes from enriched LinkedIn profile
    to fill ApplicationFormAnswers without requiring a manual form.
    """
    # 1. Current City — from location_name
    location = profile_data.get("location_name", "")
    # Try to extract city from "City, State, Country" format
    city = location.split(",")[0].strip() if location else ""

    # 2. Years of Experience — calculated from positions
    years_exp = _calculate_experience_years(profile_data.get("positions", []))

    # 3. Languages — from profile languages field
    languages = []
    raw_languages = profile_data.get("languages", [])
    for l in raw_languages:
        if isinstance(l, dict):
            languages.append(l.get("name", ""))
        elif isinstance(l, str):
            languages.append(l)
    # Default to English if no languages specified
    if not languages:
        languages = ["English"]

    # 4. Work Mode Preference — infer as flexible (we can't know from LinkedIn)
    work_mode = WorkModePreference.FLEXIBLE

    return ApplicationFormAnswers(
        current_city=city,
        open_to_relocation=False,  # Conservative default
        years_experience=years_exp,
        work_mode_preference=work_mode,
        languages=languages,
        work_authorization=None
    )


def _calculate_experience_years(positions: list) -> float:
    """
    Calculate total years of experience from LinkedIn positions.
    Uses date ranges to compute actual duration, falling back to count-based estimate.
    """
    if not positions:
        return 0.0

    from datetime import date

    total_months = 0
    today = date.today()

    for pos in positions:
        dr = pos.get("date_range", {})
        if not dr:
            # Estimate 2 years per position without dates
            total_months += 24
            continue

        start = dr.get("start", {})
        end = dr.get("end", {})

        if not start or not start.get("year"):
            total_months += 24  # fallback
            continue

        start_year = int(start.get("year", today.year))
        start_month = int(start.get("month", 1))

        if end and end.get("year"):
            end_year = int(end["year"])
            end_month = int(end.get("month", 12))
        else:
            # Currently working
            end_year = today.year
            end_month = today.month

        months = (end_year - start_year) * 12 + (end_month - start_month)
        total_months += max(months, 1)  # At least 1 month

    return round(total_months / 12.0, 1)


# ─── Role Profile Management (JSON files) ────────────────────────────

def save_role_profile(role_data: dict) -> str:
    """Save or update a role profile. Returns the role_id."""
    role_id = role_data.get("role_id") or str(uuid.uuid4())[:8]
    role_data["role_id"] = role_id
    role_data["updated_at"] = datetime.utcnow().isoformat()

    if "created_at" not in role_data:
        role_data["created_at"] = datetime.utcnow().isoformat()

    # Auto-compute total compensation
    stage1 = role_data.get("stage1", {})
    if stage1 and "compensation" in stage1:
        comp = stage1["compensation"]
        if comp.get("variable_min") is not None:
            comp["total_min"] = comp.get("fixed_min", 0) + comp["variable_min"]
        else:
            comp["total_min"] = comp.get("fixed_min", 0)
        if comp.get("variable_max") is not None:
            comp["total_max"] = comp.get("fixed_max", 0) + comp["variable_max"]
        else:
            comp["total_max"] = comp.get("fixed_max", 0)

    filepath = ROLE_PROFILES_DIR / f"{role_id}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(role_data, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"Saved role profile: {role_id}")
    return role_id


def load_role_profile(role_id: str) -> Optional[dict]:
    """Load a role profile by ID."""
    filepath = ROLE_PROFILES_DIR / f"{role_id}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def list_role_profiles() -> List[dict]:
    """List all saved role profiles (summary view)."""
    profiles = []
    for filepath in ROLE_PROFILES_DIR.glob("*.json"):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            stage1 = data.get("stage1", {})
            profiles.append({
                "role_id": data.get("role_id", filepath.stem),
                "role_title": stage1.get("role_title", "Untitled"),
                "role_type": stage1.get("role_type", "other"),
                "company_name": stage1.get("company_name", ""),
                "location": stage1.get("location", []),
                "status": data.get("status", "draft"),
                "isCompleted": data.get("isCompleted", False),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
            })
        except Exception as e:
            logger.error(f"Error loading role profile {filepath}: {e}")
    return profiles


def delete_role_profile(role_id: str) -> bool:
    """Delete a role profile."""
    filepath = ROLE_PROFILES_DIR / f"{role_id}.json"
    if filepath.exists():
        filepath.unlink()
        return True
    return False


# ─── Screening Results Management ────────────────────────────────────

def save_screening_result(handle: str, public_id: str, role_id: str, result: dict):
    """Save screening result for a candidate."""
    results_dir = SCREENING_RESULTS_DIR / handle
    results_dir.mkdir(exist_ok=True)

    # Key by public_id + role_id so same candidate can be screened for multiple roles
    filename = f"{public_id}__{role_id}.json"
    filepath = results_dir / filename

    result["public_id"] = public_id
    result["role_id"] = role_id
    result["handle"] = handle
    result["screened_at"] = datetime.utcnow().isoformat()

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"Saved screening result: {handle}/{public_id} for role {role_id}")


def load_screening_result(handle: str, public_id: str, role_id: str) -> Optional[dict]:
    """Load a specific screening result."""
    filepath = SCREENING_RESULTS_DIR / handle / f"{public_id}__{role_id}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def list_screening_results(handle: str, role_id: str = None) -> List[dict]:
    """List all screening results for a handle, optionally filtered by role."""
    results_dir = SCREENING_RESULTS_DIR / handle
    if not results_dir.exists():
        return []

    results = []
    for filepath in results_dir.glob("*.json"):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if role_id and data.get("role_id") != role_id:
                continue
            results.append(data)
        except Exception as e:
            logger.error(f"Error loading screening result {filepath}: {e}")

    # Sort by fit_score descending
    results.sort(key=lambda x: x.get("fit_score", 0) or 0, reverse=True)
    return results


# ─── Main Screening Orchestrator ─────────────────────────────────────

def screen_candidate(profile_data: dict, role_profile: dict, handle: str = "") -> dict:
    """
    Full screening pipeline for a single candidate.

    1. Auto-extract candidate attributes from LinkedIn profile
    2. Run Gate 1 structured checks
    3. If Gate 1 passes → Run Gate 1 hard filters (LLM)
    4. If hard filters pass → Run Gate 2 deep scoring (LLM)
    5. Save and return results

    Args:
        profile_data: Enriched LinkedIn profile dict (from Profile.profile column)
        role_profile: Full role profile dict (stage1 + stage2 + stage3)
        handle: Account handle for result storage

    Returns:
        Complete screening result dict
    """
    public_id = profile_data.get("public_identifier", "unknown")
    role_id = role_profile.get("role_id", "unknown")
    candidate_name = profile_data.get("full_name", public_id)

    logger.info(f"🔍 Screening {candidate_name} ({public_id}) for role {role_id}")

    # Step 1: Auto-extract candidate attributes
    app_form = extract_candidate_attributes(profile_data)
    logger.info(f"  → Extracted: city={app_form.current_city}, exp={app_form.years_experience}y, "
                f"languages={app_form.languages}")

    # Step 2: Gate 1 — Structured checks
    gate1_step1 = run_gate1_structured(app_form, role_profile)
    logger.info(f"  → Gate 1 Step 1: {gate1_step1['overall_status']}")

    gate1_step2 = None
    gate1_overall = gate1_step1["overall_status"]
    screening_status = ScreeningStatus.GATE1_PENDING

    if gate1_overall == GateStatus.FAIL:
        screening_status = ScreeningStatus.GATE1_FAILED

    elif gate1_overall == GateStatus.UNCLEAR:
        screening_status = ScreeningStatus.GATE1_NEEDS_REVIEW

    elif gate1_overall == GateStatus.PASS:
        # Step 3: Gate 1 — Hard filters (LLM)
        hard_filters = role_profile.get("stage3", {}).get("hard_filters", [])
        resume_text = synthesize_resume_text(profile_data)
        gate1_step2 = run_gate1_hard_filters(resume_text, hard_filters)
        gate1_overall = gate1_step2["overall_status"]
        logger.info(f"  → Gate 1 Step 2 (Hard Filters): {gate1_overall}")

        if gate1_overall == GateStatus.FAIL:
            screening_status = ScreeningStatus.GATE1_FAILED
        elif gate1_overall == GateStatus.UNCLEAR:
            screening_status = ScreeningStatus.GATE1_NEEDS_REVIEW
        else:
            screening_status = ScreeningStatus.GATE2_PENDING

    # Step 4: Gate 2 — Deep scoring (only if Gate 1 passed)
    gate2_result = None
    fit_score = None
    tier = None

    if screening_status == ScreeningStatus.GATE2_PENDING:
        resume_text = synthesize_resume_text(profile_data)
        gate2_result = run_gate2_scoring(
            resume_text=resume_text,
            role_profile=role_profile,
            application_form_inputs=app_form.dict()
        )
        fit_score = gate2_result.get("fit_score")
        tier = gate2_result.get("tier")
        screening_status = ScreeningStatus.GATE2_COMPLETE
        logger.info(f"  → Gate 2: score={fit_score}, tier={tier}")

    # Build result
    result = {
        "public_id": public_id,
        "candidate_name": candidate_name,
        "role_id": role_id,
        "candidate_attributes": app_form.dict(),
        "gate1_result": {
            "step1": gate1_step1,
            "step2": gate1_step2,
            "overall_status": gate1_overall,
        },
        "gate2_result": gate2_result,
        "fit_score": fit_score,
        "tier": tier,
        "screening_status": screening_status,
        "screened_at": datetime.utcnow().isoformat(),
    }

    # Save result
    if handle and public_id != "unknown":
        save_screening_result(handle, public_id, role_id, result)

    return result


def screen_batch(profiles: List[dict], role_profile: dict, handle: str = "") -> List[dict]:
    """
    Screen multiple candidates against a role profile.
    Returns list of results sorted by fit_score descending.
    """
    results = []
    total = len(profiles)

    for i, profile_data in enumerate(profiles, 1):
        name = profile_data.get("full_name", "Unknown")
        logger.info(f"[{i}/{total}] Screening {name}...")

        try:
            result = screen_candidate(profile_data, role_profile, handle)
            results.append(result)
        except Exception as e:
            logger.error(f"Error screening {name}: {e}")
            results.append({
                "public_id": profile_data.get("public_identifier", "unknown"),
                "candidate_name": name,
                "error": str(e),
                "screening_status": "error",
                "fit_score": None,
                "tier": None,
            })

    # Sort: shortlisted first, then by score
    tier_order = {"SHORTLIST": 0, "REVIEW": 1, "PASS": 2, "ERROR": 3, None: 4}
    results.sort(key=lambda x: (
        tier_order.get(x.get("tier"), 4),
        -(x.get("fit_score") or 0)
    ))

    return results
