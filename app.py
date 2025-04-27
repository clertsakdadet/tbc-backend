from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
import os

load_dotenv()

app = Flask(__name__)
CORS(app)  # Allow frontend to access backend

# --- Database Connection Function ---
def get_db_connection():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    return conn

# --- Test Route ---
@app.route("/ping")
def ping():
    return jsonify({"message": "pong"})

# --- Employees API ---
@app.route("/api/employees", methods=["GET"])
def get_employees():
    try:
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
            FROM tbc.employees;
        """)  # << If your table is inside 'tbc' schema

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        employees = [dict(zip(columns, row)) for row in rows]

        cursor.close()
        conn.close()

        return jsonify(employees)
    
    except Exception as e:
        print("Error in /api/employees:", e)
        return jsonify({"error": "Internal Server Error"}), 500

# --- Main Entry ---
if __name__ == "__main__":
    app.run(debug=True)
