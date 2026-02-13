# linkedin/api/client.py
import json
import logging
from typing import Optional, Any
from urllib.parse import urlparse

from linkedin.api.voyager import parse_linkedin_voyager_response
from linkedin.db.profiles import url_to_public_id
from linkedin.navigation.exceptions import AuthenticationError

logger = logging.getLogger(__name__)


class PlaywrightLinkedinAPI:

    def __init__(
            self,
            session: "AccountSession",
    ):
        self.session = session
        self.page = session.page
        self.context = session.context

        # Extract cookies from the browser context to get JSESSIONID for csrf-token
        cookies = self.context.cookies()
        cookies_dict = {c['name']: c['value'] for c in cookies}
        jsessionid = cookies_dict.get('JSESSIONID', '').strip('"')

        # Dynamically fetch browser-specific details using page.evaluate
        user_agent = self.page.evaluate("navigator.userAgent")
        accept_language = self.page.evaluate("navigator.languages ? navigator.languages.join(',') : navigator.language")
        sec_ch_ua = self.page.evaluate("""() => {
            if (navigator.userAgentData) {
                return navigator.userAgentData.brands.map(brand => `"${brand.brand}";v="${brand.version}"`).join(', ');
            }
            return '';
        }""")
        sec_ch_ua_mobile = self.page.evaluate(
            """() => navigator.userAgentData ? (navigator.userAgentData.mobile ? '?1' : '?0') : '?0' """)
        sec_ch_ua_platform = self.page.evaluate(
            """() => navigator.userAgentData ? `"${navigator.userAgentData.platform}"` : '' """)

        # Set up headers with dynamic values
        self.headers = {
            'accept': 'application/vnd.linkedin.normalized+json+2.1',
            'accept-language': accept_language,
            'csrf-token': jsessionid,
            'priority': 'u=1, i',
            'referer': self.page.url,
            'sec-ch-prefers-color-scheme': 'light',
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_ua_mobile,
            'sec-ch-ua-platform': sec_ch_ua_platform,
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': user_agent,
            'x-li-lang': 'en_US',
            'x-restli-protocol-version': '2.0.0',
        }

    def get_profile(
            self, public_identifier: Optional[str] = None, profile_url: Optional[str] = None
    ) -> tuple[None, None] | tuple[dict, Any]:
        if not public_identifier and profile_url:
            public_identifier = url_to_public_id(profile_url)

        if not public_identifier:
            raise ValueError("Need public_identifier or profile_url")

        params = {
            'decorationId': 'com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-91',
            'memberIdentity': public_identifier,
            'q': 'memberIdentity',
        }

        base_url = "https://www.linkedin.com/voyager/api"
        uri = "/identity/dash/profiles"
        full_url = base_url + uri

        res = self.context.request.get(full_url, params=params, headers=self.headers)

        match res.status:
            case 401:
                logger.error("LinkedIn API → 401 Unauthorized (session expired or blocked)")
                raise AuthenticationError("LinkedIn API returned 401 Unauthorized.")

            case 403 | 404:
                logger.info("Profile inaccessible → private / deleted / restricted → %s (HTTP %d)",
                           public_identifier, res.status)
                logger.debug(f"Body: {json.dumps(res.json(), indent=2)}")
                return None, None

        if not res.ok:
            body_str = res.body().decode("utf-8", errors="ignore") if isinstance(res.body(), bytes) else str(res.body())
            logger.error("API request failed → %s | Status: %s", public_identifier, res.status)
            raise Exception(f"LinkedIn API error {res.status}: {body_str[:500]}")

        data = res.json()
        extracted_info = parse_linkedin_voyager_response(data, public_identifier=public_identifier)
        return extracted_info, data

    def get_company(self, company_urn_or_id: str) -> Optional[dict]:
        """
        Fetch company details using the Voyager API.
        Accepts full URN (urn:li:fsd_company:12345) or just the ID (12345).
        """
        if not company_urn_or_id:
            return None

        # Extract numeric ID if URN is provided
        company_id = company_urn_or_id.split(':')[-1]
        
        # Using the standard organization endpoint without decoration (verified working)
        base_url = "https://www.linkedin.com/voyager/api"
        uri = f"/organization/companies/{company_id}"
        
        full_url = base_url + uri

        try:
            res = self.context.request.get(full_url, headers=self.headers)
            
            if res.status != 200:
                logger.warning(f"Company API failed → {company_urn_or_id} (HTTP {res.status})")
                return None
                
            json_data = res.json()
            data = json_data.get("data", {})
            included = json_data.get("included", [])
            
            # Resolve Industry from Included
            industry_name = None
            for item in included:
                if item.get("$type") == "com.linkedin.voyager.common.Industry":
                    industry_name = item.get("name")
                    break

            # Handle affiliated companies
            affiliated = []
            if "affiliatedCompanies" in data:
                # These usually come as a list of URNs or mini-objects
                # For now, we store the raw resolution if possible or just the list
                affiliated = data.get("affiliatedCompanies")

            # Basic parsing of the raw response to return a clean dict
            company_details = {
                "name": data.get("name"),
                "description": data.get("description"),
                "tagline": data.get("tagline"),
                "url": data.get("websiteUrl"),
                "industry": industry_name or data.get("industry", {}).get("name"), # Fallback
                "specialties": data.get("specialties", []),
                "employee_count": data.get("staffCount"),
                "headquarters": data.get("headquarters", {}).get("city"),
                "affiliated_companies": affiliated,
                "company_type": data.get("companyType", {}).get("localizedName")
            }
            return company_details
            
        except Exception as e:
            logger.error(f"Failed to fetch company {company_urn_or_id}: {e}")
            return None