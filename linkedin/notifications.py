# linkedin/notifications.py
import logging
from termcolor import colored

logger = logging.getLogger(__name__)

def send_alert(message: str, category: str = "security"):
    """
    Sends an alert for critical events like CAPTCHAs or Restrictions.
    Currently logs to terminal with high visibility. 
    Can be extended to send Email, Slack, or Discord webhooks.
    """
    prefix = "🚨 ALERT"
    if category == "security":
        color = "red"
        prefix = "🛑 SECURITY ALERT"
    elif category == "limit":
        color = "yellow"
        prefix = "⚠️ LIMIT ALERT"
    else:
        color = "cyan"

    alert_box = f"""
{'*' * 60}
{prefix}: {message}
{'*' * 60}
"""
    logger.error(colored(alert_box, color, attrs=["bold"]))
    
    # FUTURE: Add Email/Webhook logic here
    # send_webhook(message)
    # send_email(message)
