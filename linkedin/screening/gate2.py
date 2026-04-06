# linkedin/screening/gate2.py
"""
Gate 2: Multi-dimensional LLM-based fit scoring.
All prompts, weights, scoring rules, and evaluation criteria are identical to the original.
Adapted to use synchronous call_llm() instead of async aiohttp.
"""

import json
import re
import logging
from datetime import datetime

from linkedin.screening.models import GateStatus
from linkedin.templates.renderer import call_llm

logger = logging.getLogger(__name__)


# ─── Weight Presets (exact copy) ─────────────────────────────────────

WEIGHT_PRESETS = {
    "SDR":           {"motion": 0.50, "buyer": 0.15, "industry": 0.20, "seniority": 0.15},
    "BDR":           {"motion": 0.50, "buyer": 0.15, "industry": 0.20, "seniority": 0.15},
    "AE":            {"motion": 0.40, "buyer": 0.30, "industry": 0.20, "seniority": 0.10},
    "Enterprise AE": {"motion": 0.30, "buyer": 0.35, "industry": 0.25, "seniority": 0.10},
    "AM":            {"motion": 0.45, "buyer": 0.20, "industry": 0.25, "seniority": 0.10},
    "CSM":           {"motion": 0.45, "buyer": 0.20, "industry": 0.25, "seniority": 0.10},
    "Sales Manager": {"motion": 0.30, "buyer": 0.20, "industry": 0.20, "seniority": 0.30},
    "Field Sales":   {"motion": 0.45, "buyer": 0.25, "industry": 0.20, "seniority": 0.10},
    "Channel Sales": {"motion": 0.45, "buyer": 0.25, "industry": 0.20, "seniority": 0.10},
    "default":       {"motion": 0.40, "buyer": 0.25, "industry": 0.20, "seniority": 0.15},
}


# ─── Gate 2 System Prompt (exact copy) ───────────────────────────────

GATE2_SYSTEM_PROMPT = """ROLE AND OBJECTIVE
You are a senior sales hiring analyst. Evaluate whether a sales candidate's background genuinely fits the role — not whether they look impressive, but whether the way they have been selling matches what this role requires.

CORE PRINCIPLES
1. Infer activities, not titles — read bullet points, not job labels.
2. Recency weighs more — last 1–2 roles carry the most weight.
3. Classify the company, not the candidate's description of it.
4. Read between the lines on buyer persona using the inference chain.
5. Surface risk — flag mismatches clearly. Don't suppress.
6. One pass, four dimensions — read the resume once, score all four simultaneously.

INDUSTRY CLASSIFICATION (override candidate framing with these rules)
- EdTech: Byju's, Unacademy, Vedantu, Toppr, upGrad, WhiteHat Jr
- Fintech: Razorpay, Paytm, PhonePe, BharatPe, Khatabook, Cred
- B2B SaaS: Freshworks, Zoho, Leadsquared, Chargebee, CleverTap, MoEngage, Postman, Browserstack
- D2C: Mamaearth, boAt, Lenskart, Nykaa, WOW Skin Science
- Marketplace: Flipkart, Amazon India, Swiggy, Zomato, Urban Company
- IT Services: TCS, Infosys, Wipro, HCL, Tech Mahindra, Cognizant
- FMCG: HUL, ITC, Nestle, P&G, Marico, Dabur
- HR Tech: Darwinbox, Keka, GreytHR, Springworks
- BFSI: HDFC Bank, ICICI, Kotak, Bajaj Finance, LIC, PolicyBazaar
- Telecom: Airtel, Jio, BSNL, Vi
- For unlisted companies: classify from product type, buyers, and business model

SCORING RULES
Dimension 1 — Sales Motion Match (see weights in role context)
  85-100: Near-exact match, same activities+funnel+orientation
  70-84:  Strong match, minor gaps only
  55-69:  Partial match, meaningful scope/funnel differences
  40-54:  Weak match, fundamentally different but some transferable element
  0-39:   Mismatch, completely different motion
  Rules: Title ≠ Activity | Recency > history | Retail/floor sales ≠ B2B | Pre-sales ≠ quota-carrying | Flag career regression | Inbound ≠ outbound

Dimension 2 — Buyer Persona Proximity (see weights in role context)
  85-100: Same seniority + function + domain
  70-84:  One element slightly different
  55-69:  Same seniority, different function OR same function, different seniority
  40-54:  One element matches, others diverge significantly
  0-39:   No meaningful overlap
  Inference: company → product → buyers | deal size → seniority | titles mentioned | language used
  Penalty: If candidate's buyers match negative_buyer_titles, score 0–30 and flag explicitly.

Dimension 3 — Industry/Domain Relevance (see weights in role context)
  85-100: Direct match, recent/longest roles in primary industry
  70-84:  Primary industry in background, not most recent
  50-69:  Adjacent match (HM-marked adjacent industry)
  25-49:  Unrelated but not excluded
  0-24:   Excluded industry (HM explicitly said NOT a fit)
  Rules: Classify company not candidate label | Weight recent over career average | Multi-industry = nuanced scoring

Dimension 4 — Seniority/Level Calibration (see weights in role context)
  85-100: Exact level match
  70-84:  One step off in reasonable direction
  50-69:  Noticeable gap (manager applying for IC, or IC for manager with minor signals)
  30-49:  Significant mismatch
  0-29:   Extreme mismatch
  Rules: Score level not years (Gate 1 handled years) | Overqualification → 50-69 + risk flag | Readiness signals count for underqualification

TENURE: Extract employment dates, calculate durations. No judgment. If dates are unclear, estimate conservatively and note it.

CONTRADICTION CHECK: Flag ONLY factual discrepancies (experience years gap >1yr, claimed B2B but only B2C resume, claimed SaaS but all EdTech/FMCG, specific tools claimed with zero resume evidence). Do NOT flag language claims or minor phrasing differences.

OUTPUT FORMAT — Return ONLY this JSON object, nothing else:
{
  "dimensions": {
    "sales_motion_match": {
      "score": <0-100>,
      "reasoning": "<2-4 sentences>",
      "alignment_signals": ["<specific resume evidence>"],
      "risk_flags": ["<mismatch or gap>"]
    },
    "buyer_persona_proximity": {
      "score": <0-100>,
      "reasoning": "<2-4 sentences>",
      "inferred_buyer_profile": {"seniority": "", "function": "", "domain": ""},
      "alignment_signals": [],
      "risk_flags": []
    },
    "industry_relevance": {
      "score": <0-100>,
      "reasoning": "<2-4 sentences>",
      "company_classifications": [{"company": "", "industry": "", "match_type": "direct|adjacent|unrelated|excluded"}],
      "alignment_signals": [],
      "risk_flags": []
    },
    "seniority_calibration": {
      "score": <0-100>,
      "reasoning": "<2-3 sentences>",
      "inferred_level": "<individual_contributor|senior_ic|team_lead|manager|senior_manager_director|junior>",
      "alignment_signals": [],
      "risk_flags": []
    }
  },
  "tenure_summary": {
    "current_role": "",
    "previous_role": "",
    "role_before_that": "",
    "average_tenure_months": 0,
    "longest_tenure": "",
    "shortest_tenure": ""
  },
  "contradiction_flags": []
}"""


# ─── Gate 2 Scoring Function ─────────────────────────────────────────

def run_gate2_scoring(resume_text: str, role_profile: dict,
                      application_form_inputs: dict) -> dict:
    """
    Run Gate 2 multi-dimensional fit scoring.
    Uses call_llm() synchronously instead of async aiohttp.
    All scoring logic, weights, and tier assignment are identical.
    """
    stage1 = role_profile.get("stage1", {})
    stage2 = role_profile.get("stage2", {})
    role_type = stage1.get("role_type", "default")
    weights = WEIGHT_PRESETS.get(role_type, WEIGHT_PRESETS["default"])

    role_context = f"""ROLE MOTION PROFILE
Role Title: {stage1.get('role_title', 'N/A')}
Role Type: {role_type}
Company: {stage1.get('company_name', 'N/A')}

MOTION DETAILS
Key Activities: {', '.join(stage2.get('key_activities', []))}
Funnel Ownership: {stage2.get('funnel_ownership', 'N/A')}
Hunting/Farming: {stage2.get('hunting_farming', 'N/A')}
Motion Description: {stage2.get('motion_description', 'N/A')}

BUYER PROFILE
Target Buyer Domain: {', '.join(stage2.get('target_buyer_domain', []))}
Target Buyer Titles: {', '.join(stage2.get('target_buyer_titles', []))}
Negative Buyer Titles (NOT a fit): {', '.join(stage2.get('negative_buyer_titles', []))}

INDUSTRY
Primary Industry: {stage2.get('primary_industry', 'N/A')}
Adjacent Industries: {', '.join(stage2.get('adjacent_industries', []))}
Excluded Industries: {', '.join(stage2.get('excluded_industries', []))}

SENIORITY
Required Level: {stage2.get('seniority_level', 'N/A')}

SCORING WEIGHTS FOR {role_type}
Sales Motion Match: {int(weights['motion'] * 100)}%
Buyer Persona Proximity: {int(weights['buyer'] * 100)}%
Industry/Domain Relevance: {int(weights['industry'] * 100)}%
Seniority/Level Calibration: {int(weights['seniority'] * 100)}%

APPLICATION FORM (candidate self-reported — use for contradiction check)
City: {application_form_inputs.get('current_city', 'N/A')}
Years of Experience: {application_form_inputs.get('years_experience', 'N/A')}
Work Mode Preference: {application_form_inputs.get('work_mode_preference', 'N/A')}
Languages: {', '.join(application_form_inputs.get('languages', []))}

CANDIDATE RESUME
{resume_text}"""

    start_time = datetime.utcnow()

    try:
        # Combine system prompt + role context for call_llm
        full_prompt = f"""{GATE2_SYSTEM_PROMPT}

---

{role_context}"""

        raw = call_llm(full_prompt)
        latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        try:
            llm_output = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            llm_output = json.loads(match.group()) if match else {}

    except Exception as e:
        logger.error(f"Gate 2 LLM call failed: {e}")
        latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        return {
            "fit_score": 0,
            "tier": "ERROR",
            "error": str(e),
            "weights_used": weights,
            "role_type": role_type,
            "dimensions": {},
            "top_3_reasons": [],
            "risk_flags": [],
            "tenure_summary": {},
            "contradiction_flags": [],
            "model_used": "azure-openai",
            "latency_ms": latency_ms,
            "timestamp": datetime.utcnow().isoformat(),
        }

    dims = llm_output.get("dimensions", {})

    # Extract raw scores
    motion_score    = dims.get("sales_motion_match",      {}).get("score", 0)
    buyer_score     = dims.get("buyer_persona_proximity",  {}).get("score", 0)
    industry_score  = dims.get("industry_relevance",       {}).get("score", 0)
    seniority_score = dims.get("seniority_calibration",    {}).get("score", 0)

    # Annotate each dimension with weight + weighted score
    for dim_key, w_key in [
        ("sales_motion_match",     "motion"),
        ("buyer_persona_proximity","buyer"),
        ("industry_relevance",     "industry"),
        ("seniority_calibration",  "seniority"),
    ]:
        if dim_key in dims:
            dims[dim_key]["weight"]         = weights[w_key]
            dims[dim_key]["weighted_score"] = round(dims[dim_key]["score"] * weights[w_key], 2)

    # Calculate composite fit score
    fit_score = round(
        (motion_score    * weights["motion"])   +
        (buyer_score     * weights["buyer"])    +
        (industry_score  * weights["industry"]) +
        (seniority_score * weights["seniority"]),
        2
    )

    # Assign tier
    tier = "SHORTLIST" if fit_score >= 70 else ("REVIEW" if fit_score >= 55 else "PASS")

    # Deduplicate risk flags
    all_flags = []
    for d in dims.values():
        all_flags.extend(d.get("risk_flags", []))
    seen, deduped = set(), []
    for f in all_flags:
        if f not in seen:
            seen.add(f)
            deduped.append(f)

    # Top 3 alignment signals
    top_3 = [d.get("alignment_signals", [""])[0] for d in dims.values() if d.get("alignment_signals")][:3]

    return {
        "fit_score":          fit_score,
        "tier":               tier,
        "weights_used":       weights,
        "role_type":          role_type,
        "dimensions":         dims,
        "top_3_reasons":      top_3,
        "risk_flags":         deduped,
        "tenure_summary":     llm_output.get("tenure_summary", {}),
        "contradiction_flags":llm_output.get("contradiction_flags", []),
        "model_used":         "azure-openai",
        "latency_ms":         latency_ms,
        "timestamp":          datetime.utcnow().isoformat(),
    }
