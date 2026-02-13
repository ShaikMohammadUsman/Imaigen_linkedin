# linkedin/db/engine.py
import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from linkedin.api.cloud_sync import sync_profiles
from linkedin.conf import get_account_config
from linkedin.db.models import Base, Profile
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)


class Database:
    """
    One account → one database.
    Profiles are saved instantly using public_identifier as PK.
    Sync to cloud happens ONLY when close() is called.
    """

    def __init__(self, db_path: str = None):
        import os
        
        # Check for Cloud SQL connection string first
        self.db_url = os.getenv("DATABASE_URL")
        
        if self.db_url:
            logger.info("Initializing remote DB (Cloud SQL)")
            # Postgres doesn't need check_same_thread
            self.engine = create_engine(self.db_url)
        else:
            # Fallback to local SQLite
            if not db_path:
                raise ValueError("db_path required if DATABASE_URL not set")
                
            self.db_url = f"sqlite:///{db_path}"
            logger.info("Initializing local DB → %s", Path(db_path).name)
            self.engine = create_engine(self.db_url, connect_args={"check_same_thread": False})

        Base.metadata.create_all(bind=self.engine)
        
        # Only run manual column fixes for SQLite or if really needed
        if "sqlite" in self.db_url:
            self._ensure_columns_exist()
            
        logger.debug("DB schema ready")

        session_factory = sessionmaker(bind=self.engine)
        self.Session = scoped_session(session_factory)
        self.db_path = Path(db_path) if db_path else None

    def get_session(self):
        return self.Session()

    def close(self):
        logger.info("DB.close() → syncing all unsynced profiles to cloud...")
        self._sync_all_unsynced_profiles()
        self.Session.remove()
        logger.info("DB closed and fully synced with cloud")

    def _ensure_columns_exist(self):
        """
        Manually add missing columns if they don't exist in SQLite.
        Base.metadata.create_all doesn't handle migrations.
        """
        from sqlalchemy import inspect, text
        inspector = inspect(self.engine)
        columns = [c['name'] for c in inspector.get_columns('profiles')]
        
        with self.engine.connect() as conn:
            if 'last_message' not in columns:
                logger.info("Adding column 'last_message' to profiles table")
                conn.execute(text("ALTER TABLE profiles ADD COLUMN last_message TEXT"))
                conn.commit()
            if 'last_message_at' not in columns:
                logger.info("Adding column 'last_message_at' to profiles table")
                conn.execute(text("ALTER TABLE profiles ADD COLUMN last_message_at DATETIME"))
                conn.commit()
            if 'last_received_message' not in columns:
                logger.info("Adding column 'last_received_message' to profiles table")
                conn.execute(text("ALTER TABLE profiles ADD COLUMN last_received_message TEXT"))
                conn.commit()
            if 'last_received_at' not in columns:
                logger.info("Adding column 'last_received_at' to profiles table")
                conn.execute(text("ALTER TABLE profiles ADD COLUMN last_received_at DATETIME"))
                conn.commit()

    def _sync_all_unsynced_profiles(self):
        with self.get_session() as db_session:
            # Fixed: was filtering on non-existent `scraped` column
            unsynced = db_session.query(Profile).filter_by(
                cloud_synced=False
            ).filter(Profile.profile.isnot([ProfileState.DISCOVERED.value])).all()

            if not unsynced:
                logger.info("All profiles already synced")
                return

            payload = [p.data for p in unsynced if p.data]
            if not payload:
                return

            success = sync_profiles(payload)

            if success:
                for p in unsynced:
                    p.cloud_synced = True
                db_session.commit()
                logger.info("Synced %s new profile(s) to cloud", len(payload))
            else:
                logger.error("Cloud sync failed — will retry on next close()")

    @classmethod
    def from_handle(cls, handle: str) -> "Database":
        logger.info("Spinning up DB for @%s", handle)
        config = get_account_config(handle)
        db_path = config["db_path"]
        logger.debug("DB path → %s", db_path)
        return cls(db_path)
