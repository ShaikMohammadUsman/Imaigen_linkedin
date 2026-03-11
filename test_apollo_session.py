import logging
import sys
from scooter_apollo.sessions import ApolloSessionManager
from termcolor import colored

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

manager = ApolloSessionManager()
try:
    print("🚀 Testing session for usman@thescooter.ai...")
    session = manager.get_session(
        email="usman@thescooter.ai",
        password="Crisp@Trump123456",
        login_method="google"
    )
    if session:
        print(f"✅ Current URL: {session.page.url}")
        session.page.screenshot(path="session_test_result.png")
        print("📸 Screenshot saved to session_test_result.png")
    else:
        print("❌ Failed to get session.")
finally:
    manager.close()
