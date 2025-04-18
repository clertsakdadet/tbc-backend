from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
import os

load_dotenv()
app = Flask(__name__)
CORS(app)  # Allows frontend (Vercel) to access backend

@app.route("/ping")
def ping():
    return jsonify({"message": "pong"})

# Establish connection
def get_db_connection():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    return conn

@app.route("/api/employees", methods=["GET"])
def get_employees():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            id,
            first_name,
            preferred_name,
            middle_name,
            last_name,
            role,
            phone_number,
            email,
            hire_date,
            end_date,
            active,
            position,
            address
        FROM employees;
    """)
    
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]

    # Convert rows to list of dicts
    employees = [dict(zip(columns, row)) for row in rows]

    cursor.close()
    conn.close()

    return jsonify(employees)


if __name__ == "__main__":
    app.run(debug=True)
