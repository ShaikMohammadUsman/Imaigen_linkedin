import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

def migrate():
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("No DATABASE_URL found.")
        return
        
    print("Connecting to DB...")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            # Change the type. USING is needed in Postgres if it can't cast automatically, 
            # though int to varchar usually is automatic.
            print("Running ALTER TABLE command...")
            conn.execute(text("ALTER TABLE profiles ALTER COLUMN last_job_id TYPE VARCHAR;"))
            print("Successfully migrated last_job_id column to VARCHAR.")
    except Exception as e:
        print("Failed to run migration:", e)

if __name__ == "__main__":
    migrate()
