import sqlite3
import json

db_path = r'f:\Imaigen\scooter_ai\linkedin_usman\Scooter_Linkedin-main\Scooter_Linkedin-fix_issues_latest\Scooter_Linkedin-fix_issues\assets\data\jane_doe_main.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT public_identifier, state FROM profiles WHERE state != 'discovered' LIMIT 5")
rows = cursor.fetchall()

print("Enriched Candidates:")
for row in rows:
    print(f"ID: {row[0]}, State: {row[1]}")

conn.close()
