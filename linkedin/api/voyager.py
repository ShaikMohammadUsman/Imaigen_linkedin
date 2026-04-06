# linkedin/api/voyager.py
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Literal, Any

ConnectionDistance = Literal["DISTANCE_1", "DISTANCE_2", "DISTANCE_3", "OUT_OF_NETWORK", None]

DISTANCE_TO_DEGREE: Dict[str, Optional[int]] = {
    "DISTANCE_1": 1,
    "DISTANCE_2": 2,
    "DISTANCE_3": 3,
    "OUT_OF_NETWORK": None,
}


# ======================
# Internal dataclasses (only used for validation & structure)
# ======================

@dataclass
class Date:
    year: Optional[int] = None
    month: Optional[int] = None


@dataclass
class DateRange:
    start: Optional[Date] = None
    end: Optional[Date] = None


@dataclass
class Position:
    title: str
    company_name: str
    company_urn: Optional[str] = None
    location: Optional[str] = None
    date_range: Optional[DateRange] = None
    description: Optional[str] = None
    urn: Optional[str] = None


@dataclass
class Education:
    school_name: str
    degree_name: Optional[str] = None
    field_of_study: Optional[str] = None
    date_range: Optional[DateRange] = None
    urn: Optional[str] = None

@dataclass
class Certification:
    name: str
    authority: Optional[str] = None
    license_number: Optional[str] = None
    display_source: Optional[str] = None
    url: Optional[str] = None
    date_range: Optional[DateRange] = None
    urn: Optional[str] = None

@dataclass
class Project:
    title: str
    description: Optional[str] = None
    url: Optional[str] = None
    date_range: Optional[DateRange] = None
    urn: Optional[str] = None


@dataclass
class LinkedInProfile:
    url: str
    urn: str
    full_name: str
    first_name: str
    last_name: str

    headline: Optional[str] = None
    summary: Optional[str] = None
    public_identifier: Optional[str] = None
    location_name: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    geo: Optional[Dict[str, Any]] = None
    industry: Optional[Dict[str, Any]] = None

    positions: List[Position] = field(default_factory=list)
    educations: List[Education] = field(default_factory=list)
    certifications: List[Certification] = field(default_factory=list)
    projects: List[Project] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    profile_picture: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    connection_distance: Optional[ConnectionDistance] = None
    connection_degree: Optional[int] = None


# ======================
# Private helpers
# ======================

def _resolve_references(data: dict) -> Dict[str, dict]:
    """Build urn → entity lookup from 'included' array."""
    return {
        entity.get("entityUrn"): entity
        for entity in data.get("included", [])
        if entity.get("entityUrn")
    }
def _resolve_star_field(entity: dict, urn_map: Dict[str, dict], field_name: str) -> Any:
    """Resolve *company, *school, *elements, etc."""
    value = entity.get(field_name)
    if not value:
        return None
    if isinstance(value, list):
        return [urn_map.get(urn) for urn in value if urn_map.get(urn)]
    return urn_map.get(value)


def _date_from_raw(raw: Optional[dict]) -> Optional[Date]:
    if not raw:
        return None
    return Date(year=raw.get("year"), month=raw.get("month"))


def _date_range_from_raw(raw: Optional[dict]) -> Optional[DateRange]:
    if not raw:
        return None
    return DateRange(
        start=_date_from_raw(raw.get("start")),
        end=_date_from_raw(raw.get("end")),
    )



def _get_text(entity: dict, key: str, default: str = "") -> str:
    """Handle both plain string and multiLocale string objects."""
    val = entity.get(key)
    if isinstance(val, str):
        return val
    
    # Try multiLocale version
    ml_key = f"multiLocale{key[0].upper()}{key[1:]}"
    ml_val = entity.get(ml_key)
    if isinstance(ml_val, dict):
        # Prefer en_US or just the first available
        return ml_val.get("en_US") or next(iter(ml_val.values()), default)
    
    return val if isinstance(val, str) else default


def _enrich_position(pos: dict, urn_map: Dict[str, dict]) -> Position:
    company = _resolve_star_field(pos, urn_map, "*company")

    return Position(
        title=_get_text(pos, "title", "Unknown Title"),
        company_name=company.get("name") if company else pos.get("companyName", "Unknown Company"),
        company_urn=company.get("entityUrn") if company else pos.get("companyUrn"),
        location=pos.get("locationName"),
        date_range=_date_range_from_raw(pos.get("dateRange") or pos.get("timePeriod")),
        description=_get_text(pos, "description"),
        urn=pos.get("entityUrn"),
    )


def _enrich_education(edu: dict, urn_map: Dict[str, dict]) -> Education:
    school = _resolve_star_field(edu, urn_map, "*school")

    return Education(
        school_name=school.get("name") if school else edu.get("schoolName", "Unknown School"),
        degree_name=edu.get("degreeName"),
        field_of_study=edu.get("fieldOfStudy"),
        date_range=_date_range_from_raw(edu.get("dateRange") or edu.get("timePeriod")),
        urn=edu.get("entityUrn"),
    )


def _enrich_certification(cert: dict, urn_map: Dict[str, dict]) -> Certification:
    return Certification(
        name=_get_text(cert, "name", "Unknown Certification"),
        authority=cert.get("authorityName"),
        license_number=cert.get("licenseNumber"),
        display_source=cert.get("displaySource"),
        url=cert.get("url"),
        date_range=_date_range_from_raw(cert.get("timePeriod") or cert.get("dateRange")),
        urn=cert.get("entityUrn"),
    )


def _enrich_project(proj: dict, urn_map: Dict[str, dict]) -> Project:
    return Project(
        title=_get_text(proj, "title", "Unknown Project"),
        description=_get_text(proj, "description"),
        url=proj.get("url"),
        date_range=_date_range_from_raw(proj.get("timePeriod") or proj.get("dateRange")),
        urn=proj.get("entityUrn"),
    )


def _extract_connection_info(profile_entity: dict, urn_map: Dict[str, dict]) -> tuple[Optional[str], Optional[int]]:
    member_rel_urn = profile_entity.get("*memberRelationship")
    if not member_rel_urn:
        return None, None

    rel = urn_map.get(member_rel_urn)
    if not rel:
        return None, None

    union = rel.get("memberRelationshipUnion") or rel.get("memberRelationshipData")
    if not union:
        return None, None

    if "connectedMember" in union or "connected" in union:
        return "DISTANCE_1", 1

    if "noConnection" in union:
        distance_str = union["noConnection"].get("memberDistance")
        degree = DISTANCE_TO_DEGREE.get(distance_str)
        return distance_str, degree

    return None, None


# ======================
# Public function – returns plain dict
# ======================

def parse_linkedin_voyager_response(
        json_response: dict,
        public_identifier: Optional[str] = None,
) -> dict:
    """
    Parse a full LinkedIn Voyager profile response and return a clean dictionary.

    Uses dataclasses internally for validation and structure,
    but returns a plain, JSON-serializable dict (no dataclass leakage).

    Args:
        json_response: Raw JSON from Voyager API (with "data" and "included")
        public_identifier: Optional filter – only parse profile with this public ID

    Returns:
        dict with clean, structured LinkedIn profile data
    """
    urn_map = _resolve_references(json_response)

    # Find the main Profile entity
    profile_entity = None
    for entity in json_response.get("included", []):
        if entity.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile":
            entity_id = entity.get("publicIdentifier")
            if public_identifier is None or entity_id == public_identifier:
                profile_entity = entity
                break

    # Fallback if not found via $type
    if not profile_entity:
        main_urn = json_response.get("data", {}).get("*elements", [None])[0]
        profile_entity = urn_map.get(main_urn)

    if not profile_entity:
        raise ValueError("Could not find profile entity in the Voyager response")

    first_name = profile_entity.get("firstName", "")
    last_name = profile_entity.get("lastName", "")

    # Extract connection info
    connection_distance, connection_degree = _extract_connection_info(profile_entity, urn_map)

    # Geo Resolution
    geo = _resolve_star_field(profile_entity, urn_map, "*geo")
    if not geo and "geoLocation" in profile_entity:
        geo = _resolve_star_field(profile_entity.get("geoLocation", {}), urn_map, "*geo")
        
    country_name = None
    state_name = None
    full_location = profile_entity.get("locationName")
    
    if geo:
        # Priority: use the full localized name from geo if available
        geo_name = geo.get("defaultLocalizedName")
        if geo_name:
            full_location = geo_name
            
        country_entity = _resolve_star_field(geo, urn_map, "*country")
        if country_entity:
            country_name = country_entity.get("defaultLocalizedName")
            
        # Try to extract state from "Cambridge, Massachusetts" or similar
        # We want the part WITHOUT the country.
        localized_no_country = geo.get("defaultLocalizedNameWithoutCountryName")
        if localized_no_country and "," in localized_no_country:
            parts = [p.strip() for p in localized_no_country.split(",")]
            if len(parts) >= 2:
                state_name = parts[-1]
        elif full_location and "," in full_location:
            # Fallback for complex strings: "City, State, Country"
            parts = [p.strip() for p in full_location.split(",")]
            if len(parts) >= 3:
                state_name = parts[-2]
            elif len(parts) == 2:
                state_name = parts[-1]
 

    # Build positions
    positions: List[Position] = []
    pos_groups_urn = profile_entity.get("*profilePositionGroups")
    if pos_groups_urn:
        pos_groups_resp = urn_map.get(pos_groups_urn)
        if pos_groups_resp and pos_groups_resp.get("*elements"):
            for group_urn in pos_groups_resp["*elements"]:
                group = urn_map.get(group_urn)
                if not group:
                    continue
                positions_coll_urn = group.get("*profilePositionInPositionGroup")
                if positions_coll_urn:
                    positions_coll = urn_map.get(positions_coll_urn)
                    if positions_coll and positions_coll.get("*elements"):
                        for pos_urn in positions_coll["*elements"]:
                            pos = urn_map.get(pos_urn)
                            if pos:
                                positions.append(_enrich_position(pos, urn_map))

    # Build educations
    educations: List[Education] = []
    educations_urn = profile_entity.get("*profileEducations")
    if educations_urn:
        edu_coll = urn_map.get(educations_urn)
        if edu_coll and edu_coll.get("*elements"):
            for edu_urn in edu_coll["*elements"]:
                edu = urn_map.get(edu_urn)
                if edu:
                    educations.append(_enrich_education(edu, urn_map))

    # Build Certifications
    certifications: List[Certification] = []
    cert_urn = profile_entity.get("*profileCertifications")
    if cert_urn:
        cert_coll = urn_map.get(cert_urn)
        if cert_coll and cert_coll.get("*elements"):
            for urn in cert_coll["*elements"]:
                cert = urn_map.get(urn)
                if cert:
                    certifications.append(_enrich_certification(cert, urn_map))

    # Build Projects
    projects: List[Project] = []
    proj_urn = profile_entity.get("*profileProjects")
    if proj_urn:
        proj_coll = urn_map.get(proj_urn)
        if proj_coll and proj_coll.get("*elements"):
            for urn in proj_coll["*elements"]:
                proj = urn_map.get(urn)
                if proj:
                    projects.append(_enrich_project(proj, urn_map))

    # Build Skills
    skills: List[str] = []
    skills_urn = profile_entity.get("*profileSkills")
    if skills_urn:
        skills_resp = urn_map.get(skills_urn)
        if skills_resp and skills_resp.get("*elements"):
            for skill_urn in skills_resp["*elements"]:
                skill_entity = urn_map.get(skill_urn)
                if skill_entity:
                    name = skill_entity.get("name")
                    if name:
                        skills.append(name)

    # Profile Picture Extraction
    profile_picture = None
    display_image_urn = profile_entity.get("displayImage")
    if display_image_urn:
        img_resp = urn_map.get(display_image_urn)
        if img_resp:
            root_url = img_resp.get("rootUrl", "")
            artifacts = img_resp.get("artifacts", [])
            if artifacts:
                # Prefer the largest artifact
                best_artifact = artifacts[-1]
                path = best_artifact.get("fileIdentifyingNodePathSegment", "")
                if root_url and path:
                    from urllib.parse import urljoin
                    profile_picture = urljoin(root_url, path)

    # Assemble data for dataclass validation
    profile_data = {
        "urn": profile_entity.get("entityUrn"),
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip(),
        "headline": profile_entity.get("headline") or profile_entity.get("occupation"),
        "summary": profile_entity.get("summary"),
        "public_identifier": profile_entity.get("publicIdentifier"),
        "location_name": full_location,
        "state": state_name,
        "geo": geo,
        "country": country_name,
        "industry": _resolve_star_field(profile_entity, urn_map, "*industry"),
        "url": f"https://www.linkedin.com/in/{profile_entity.get('publicIdentifier', '')}/",
        "positions": [asdict(p) for p in positions],
        "educations": [asdict(e) for e in educations],
        "certifications": [asdict(c) for c in certifications],
        "projects": [asdict(p) for p in projects],
        "skills": skills,
        "profile_picture": profile_picture,
        "email": profile_entity.get("emailAddress"), # Sometimes available in certain API contexts
        "phone": profile_entity.get("phoneNumbers", [{}])[0].get("number") if profile_entity.get("phoneNumbers") else None,
        "connection_distance": connection_distance,
        "connection_degree": connection_degree,
    }

    # Validate with dataclass (will raise if something is wrong)
    profile_obj = LinkedInProfile(**profile_data)

    # Return clean dictionary – perfect for JSON, APIs, logging, etc.
    return asdict(profile_obj)
