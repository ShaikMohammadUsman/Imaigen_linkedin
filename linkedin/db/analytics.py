
import logging
from typing import Optional
from sqlalchemy.orm import Session
from linkedin.db.models import Profile, MessageEntry
from linkedin.templates.renderer import call_llm

logger = logging.getLogger(__name__)

def analyze_conversation(session: Session, profile_id: str) -> Optional[dict]:
    """
    Summarize the conversation and detect sentiment for a profile.
    Updates the Profile record in the database.
    """
    try:
        profile = session.query(Profile).filter_by(public_identifier=profile_id).first()
        if not profile or not profile.messages:
            return None

        # Build transcript string
        transcript_parts = []
        for msg in profile.messages:
            sender = "Candidate" if msg.direction == "incoming" else "Recruiter (Me)"
            transcript_parts.append(f"[{msg.timestamp.strftime('%Y-%m-%d %H:%M')}] {sender}: {msg.text}")
        
        transcript_text = "\n".join(transcript_parts)

        # AI Prompt
        prompt = f"""
        Analyze the following LinkedIn recruitment conversation transcript between a Recruiter and a Candidate.
        ---
        TRANSCRIPT:
        {transcript_text}
        ---
        Please provide:
        1. A concise 1-sentence summary of the current conversation state/status.
        2. A sentiment tag (e.g., Interested, Neutral, Not Interested, Hostile, Out of Office).
        
        Format your response EXACTLY as follow:
        SUMMARY: [Your summary here]
        SENTIMENT: [Your sentiment tag here]
        """

        response = call_llm(prompt)
        
        # Parse response
        summary = ""
        sentiment = ""
        for line in response.split('\n'):
            if line.startswith("SUMMARY:"):
                summary = line.replace("SUMMARY:", "").strip()
            elif line.startswith("SENTIMENT:"):
                sentiment = line.replace("SENTIMENT:", "").strip()

        if summary:
            profile.conversation_summary = summary
        if sentiment:
            profile.conversation_sentiment = sentiment
        
        session.commit()
        return {"summary": summary, "sentiment": sentiment}

    except Exception as e:
        logger.error(f"Error analyzing conversation for {profile_id}: {e}")
        return None
