# linkedin/screening/models.py
"""
All enums and Pydantic models for the screening architecture.
Kept identical to the original schema definitions.
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────

class WorkModeEnum(str, Enum):
    IN_OFFICE = "in_office"
    HYBRID    = "hybrid"
    REMOTE    = "remote"

class RoleTypeEnum(str, Enum):
    SDR   = "SDR"
    AE    = "AE"
    AM    = "AM"
    CSM   = "CSM"
    SE    = "SE"
    OTHER = "other"

class FunnelOwnershipEnum(str, Enum):
    TOP_OF_FUNNEL    = "top_of_funnel"
    MIDDLE_OF_FUNNEL = "middle_of_funnel"
    FULL_FUNNEL      = "full_funnel"

class HuntingFarmingEnum(str, Enum):
    MOSTLY_HUNTING = "mostly_hunting"
    MOSTLY_FARMING = "mostly_farming"
    BALANCED       = "balanced"

class SeniorityLevelEnum(str, Enum):
    INDIVIDUAL_CONTRIBUTOR = "individual_contributor"
    TEAM_LEAD              = "team_lead"
    MANAGER                = "manager"
    DIRECTOR               = "director"

class JobStatusEnum(str, Enum):
    DRAFT    = "draft"
    ACTIVE   = "active"
    INACTIVE = "inactive"
    CLOSED   = "closed"

class WorkModePreference(str, Enum):
    IN_OFFICE = "in_office"
    HYBRID    = "hybrid"
    REMOTE    = "remote"
    FLEXIBLE  = "flexible"

class GateStatus(str, Enum):
    PASS    = "PASS"
    FAIL    = "FAIL"
    UNCLEAR = "UNCLEAR"

class ScreeningStatus(str, Enum):
    GATE1_PENDING      = "gate1_pending"
    GATE1_NEEDS_REVIEW = "gate1_needs_review"
    GATE1_FAILED       = "gate1_failed"
    GATE2_PENDING      = "gate2_pending"
    GATE2_COMPLETE     = "gate2_complete"


# ─── Role Profile Models (Stages 1-3) ────────────────────────────────

class ExperienceRange(BaseModel):
    min: int
    max: int
    buffer: int = Field(default=1, description="System-applied buffer (default 1 year)")

class Compensation(BaseModel):
    fixed_min: int
    fixed_max: int
    variable_min: Optional[int] = None
    variable_max: Optional[int] = None
    total_min: Optional[int] = None
    total_max: Optional[int] = None
    currency: str = "INR"
    show_on_jd: bool = True

class Stage1RoleBasics(BaseModel):
    role_title: str
    role_type: RoleTypeEnum
    company_name: str
    company_description: str
    location: List[str]
    remote: bool = False
    timezone: str
    work_mode: WorkModeEnum
    days_in_office: Optional[int] = None
    experience_range: ExperienceRange
    compensation: Compensation
    languages: List[str]

class Stage2RoleMotion(BaseModel):
    key_activities: List[str]
    funnel_ownership: FunnelOwnershipEnum
    hunting_farming: HuntingFarmingEnum
    motion_description: str
    target_buyer_domain: List[str]
    target_buyer_titles: List[str]
    negative_buyer_titles: List[str]
    primary_industry: str
    adjacent_industries: List[str]
    excluded_industries: List[str]
    seniority_level: SeniorityLevelEnum

class TenureFilter(BaseModel):
    enabled: bool = False
    min_months: Optional[int] = None

class Stage3FiltersAndJD(BaseModel):
    hard_filters: List[str] = Field(
        default=[],
        max_length=3,
        description="Max 3 hard knockout filters used in Gate 1"
    )
    tenure_filter: TenureFilter = TenureFilter()
    jd_content: str
    status: JobStatusEnum = JobStatusEnum.DRAFT


# ─── Application Form (auto-inferred from LinkedIn profile) ──────────

class ApplicationFormAnswers(BaseModel):
    current_city: str
    open_to_relocation: bool = False
    years_experience: float
    work_mode_preference: WorkModePreference
    languages: List[str]
    work_authorization: Optional[str] = None


# ─── Full Role Profile ───────────────────────────────────────────────

class RoleProfile(BaseModel):
    """Complete role profile combining all 3 stages."""
    role_id: Optional[str] = None
    stage1: Optional[Stage1RoleBasics] = None
    stage2: Optional[Stage2RoleMotion] = None
    stage3: Optional[Stage3FiltersAndJD] = None
    isCompleted: bool = False
    status: str = "draft"
    created_at: Optional[str] = None
