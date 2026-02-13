
import sys
import logging
from linkedin.sessions.registry import get_session
from linkedin.api.client import PlaywrightLinkedinAPI

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestCompany")

def test_company_fetch(handle, company_id):
    logger.info(f"Testing company fetch for ID: {company_id}")
    
    session = get_session(handle)
    session.ensure_browser()
    
    api = PlaywrightLinkedinAPI(session)
    
    base_url = "https://www.linkedin.com/voyager/api"
    
    # Try 3: Simple, No Decoration
    print(f"\n--- Trying Simple ID: {company_id} ---")
    uri = f"/organization/companies/{company_id}"
    full_url = base_url + uri
    res = api.context.request.get(full_url, headers=api.headers)
    
    if res.status == 200:
        print("✅ Success Simple!")
        data = res.json()
        
        # Check main data
        company_data = data.get("data", {})
        print("\n--- Main Data Keys ---")
        print(company_data.keys())
        
        # Check for valuable fields
        print(f"Name: {company_data.get('name')}")
        print(f"Tagline: {company_data.get('tagline')}")
        print(f"Description Length: {len(company_data.get('description', ''))}")
        
        if "specialties" in company_data: 
            print("Found specialties")
            
        if "affiliatedCompanies" in company_data:
            print("Found affiliatedCompanies in Main Data")
            
        # Check included entities
        included = data.get("included", [])
        print(f"\n--- Included Entities: {len(included)} ---")
        types = set(i.get("$type") for i in included)
        print(f"Types found: {types}")
        
        # Check for related companies in included
        for item in included:
            if "Company" in item.get("$type", ""):
                print(f"  - Company found: {item.get('name')}")
    else:
        print(f"❌ Failed Simple (HTTP {res.status})")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python test_company.py <handle> <company_id>")
        sys.exit(1)
        
    handle = sys.argv[1]
    company_id = sys.argv[2]
    
    test_company_fetch(handle, company_id)
