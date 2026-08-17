import psycopg2
import os

try:
    conn = psycopg2.connect(dbname='jobs', user='postgres', password='santos', host='127.0.0.1', port='5432')
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'jobs_notification';")
    print("Columns:", [row[0] for row in cur.fetchall()])
    
    # Try the same for the DATABASE_URL to see if there's a mismatch
    from dotenv import load_dotenv
    load_dotenv()
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        try:
            conn2 = psycopg2.connect(db_url)
            cur2 = conn2.cursor()
            cur2.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'jobs_notification';")
            print("Neon Columns:", [row[0] for row in cur2.fetchall()])
        except Exception as e:
            print("Failed connecting to Neon:", e)
except Exception as e:
    print("Error:", e)
