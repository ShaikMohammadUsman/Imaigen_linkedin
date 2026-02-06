# linkedin/actions/chat.py
import logging
from typing import Dict, Any, List
from linkedin.sessions.registry import get_session
from linkedin.navigation.utils import goto_page

logger = logging.getLogger(__name__)

def fetch_latest_messages(handle: str, profile: Dict[str, Any], limit: int = 5) -> List[Dict[str, str]]:
    """
    Open the chat with a specific profile and scrape the latest messages.
    """
    session = get_session(handle=handle)
    session.ensure_browser()
    page = session.page
    
    public_identifier = profile.get("public_identifier")
    full_name = profile.get("full_name")
    
    logger.info(f"Fetching chat history for {public_identifier}...")
    
    try:
        # Strategy 1: Try to open messaging directly via URL
        messaging_url = f"https://www.linkedin.com/messaging/thread/new/?recipient={public_identifier}"
        
        try:
            page.goto(messaging_url, timeout=15000)
            session.wait(2, 3)
            logger.debug(f"Opened messaging via direct URL")
        except Exception as e:
            logger.debug(f"Direct messaging URL failed: {e}, trying profile approach...")
            
            # Strategy 2: Go to profile and click Message button
            profile_url = profile.get("url")
            if not profile_url.endswith('/'):
                profile_url += '/'
                
            page.goto(profile_url, timeout=15000)
            session.wait(1, 2)
            
            # Try multiple selectors for the Message button
            message_clicked = False
            
            # Try direct Message button
            try:
                direct_msg = page.locator('button:has-text("Message"):visible').first
                if direct_msg.count() > 0:
                    direct_msg.click(timeout=5000)
                    message_clicked = True
                    logger.debug("Clicked direct Message button")
            except:
                pass
            
            # Try More menu if direct didn't work
            if not message_clicked:
                try:
                    more_btn = page.locator('button[aria-label*="More actions"]:visible, button[id*="overflow"]:visible').first
                    if more_btn.count() > 0:
                        more_btn.click(timeout=5000)
                        session.wait(0.5, 1)
                        msg_option = page.locator('div:has-text("Message"):visible, a:has-text("Message"):visible').first
                        msg_option.click(timeout=5000)
                        message_clicked = True
                        logger.debug("Clicked Message via More menu")
                except:
                    pass
            
            if not message_clicked:
                logger.warning(f"Could not open messaging for {public_identifier}")
                return []
            
            session.wait(2, 3)
        
        # Extract messages from the conversation
        messages = []
        
        # Wait for messages to load
        try:
            page.wait_for_selector('.msg-s-event-listitem, .msg-s-message-list__event', timeout=10000)
        except:
            logger.debug("No messages found in conversation")
            return []
        
        # Get all message items
        message_items = page.locator('.msg-s-event-listitem').all()
        
        for item in message_items[-limit:]:  # Only get last N messages
            try:
                # Try to get sender name
                sender = "Unknown"
                try:
                    sender_elem = item.locator('.msg-s-message-group__name, .msg-s-message-group__profile-link').first
                    if sender_elem.count() > 0:
                        sender = sender_elem.text_content().strip()
                except:
                    pass
                
                # Get message text
                text = ""
                try:
                    text_elem = item.locator('.msg-s-event-listitem__body, .msg-s-message-list__event-text').first
                    if text_elem.count() > 0:
                        text = text_elem.text_content().strip()
                except:
                    pass
                
                if text:  # Only add if we got actual text
                    messages.append({
                        "sender": sender,
                        "text": text
                    })
            except Exception as e:
                logger.debug(f"Error parsing message item: {e}")
                continue
        
        logger.info(f"Found {len(messages)} messages for {public_identifier}")
        return messages
        
    except Exception as e:
        logger.error(f"Failed to fetch messages for {public_identifier}: {e}")
        return []
    finally:
        # Close popup if it's open
        try:
            page.keyboard.press("Escape")
            session.wait(0.5, 1)
        except:
            pass
