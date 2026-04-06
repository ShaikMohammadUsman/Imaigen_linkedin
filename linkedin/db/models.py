from sqlalchemy import Column, String, JSON, DateTime, Boolean, Integer, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Profile(Base):
    __tablename__ = 'profiles'

    # USING public_identifier as primary key
    public_identifier = Column(String, primary_key=True)

    # Parsed / cleaned data (what you return from get_profile)
    profile = Column(JSON, nullable=True)

    # Full raw JSON from LinkedIn's API (for debugging, re-parsing, etc.)
    data = Column(JSON, nullable=True)

    # Whether this profile has been sent to your backend / cloud / CRM
    cloud_synced = Column(Boolean, default=False, server_default='false', nullable=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    state = Column(String, nullable=False, default="discovered")
    
    # Forensic Analytics
    last_job_id = Column(String, nullable=True)
    
    # Messaging history (Outgoing)
    last_message = Column(String, nullable=True)
    last_message_at = Column(DateTime, nullable=True)

    # Messaging history (Incoming)
    last_received_message = Column(String, nullable=True)
    last_received_at = Column(DateTime, nullable=True)

    # Conversation Analytics (AI Generated)
    conversation_summary = Column(String, nullable=True)
    conversation_sentiment = Column(String, nullable=True)

    # Relationships
    messages = relationship("MessageEntry", back_populates="profile", cascade="all, delete-orphan", order_by="MessageEntry.timestamp")


class MessageEntry(Base):
    __tablename__ = 'message_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(String, ForeignKey('profiles.public_identifier'), nullable=False)
    direction = Column(String, nullable=False)  # 'incoming' or 'outgoing'
    sender_name = Column(String, nullable=True)
    text = Column(String, nullable=False)
    timestamp = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    profile = relationship("Profile", back_populates="messages")


class JobStatus(Base):
    __tablename__ = 'job_status'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    handle = Column(String, nullable=False)
    job_type = Column(String, nullable=False)  # e.g., 'enrich_profiles', 'apollo_harvest', 'clay_harvest'
    status = Column(String, nullable=False, default='running') # 'running', 'completed', 'failed', 'stopped'
    profiles_processed = Column(Integer, default=0)
    expected_limit = Column(Integer, nullable=True)
    error_message = Column(String, nullable=True)
    
    start_time = Column(DateTime, server_default=func.now(), nullable=False)
    end_time = Column(DateTime, nullable=True)