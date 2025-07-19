from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
import requests
from flask import request, jsonify
import os   

load_dotenv()

app = Flask(__name__)
CORS(app)  # Allows frontend (Vercel) to access backend

CLOVER_BASE_URL = "https://api.clover.com/v3/merchants"
CLOVER_MERCHANT_ID = os.getenv("MERCHANT_ID")
CLOVER_ACCESS_TOKEN = os.getenv("AUTHORIZATION_TOKEN")  # or load from .env

# Establish connection
def get_db_connection():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    return conn

# --- Test Route ---
@app.route("/ping")
def ping():
    return jsonify({"message": "pong"})

@app.route("/api/fetch-clover-shifts", methods=["POST"])
def fetch_clover_shifts():
    data = request.get_json()
    start_time = data.get("start_time")  # UNIX epoch (ms)
    end_time = data.get("end_time")      # UNIX epoch (ms)

    if not start_time or not end_time:
        return jsonify({"error": "start_time and end_time are required"}), 400

    url = f"{CLOVER_BASE_URL}/{CLOVER_MERCHANT_ID}/shifts"
    headers = {
        "Authorization": f"Bearer {CLOVER_ACCESS_TOKEN}",
        "Accept": "application/json"
    }
    params = {
        "expand": "employee",
        "filter": [
            "has_in_time=true",
            f"in_and_override_time>{start_time}",
            f"in_and_override_time<{end_time}"
        ]
    }

    try:
        res = requests.get(url, headers=headers, params=params)
        res.raise_for_status()
        data = res.json().get("elements", [])

        # Optional: Normalize data for preview
        shifts = []
        for s in data:
            employee = s.get("employee", {})
            shifts.append({
                "shift_id": s.get("id"),
                "clover_employee_id": employee.get("id"),
                "employee_name": employee.get("name"),
                "start": s.get("inTime"),     # or use 'inAndOverrideTime'
                "end": s.get("outTime"),      # or 'outAndOverrideTime'
                "override": {
                    "in": s.get("inAndOverrideTime"),
                    "out": s.get("outAndOverrideTime")
                }
            })

        return jsonify(shifts)

    except Exception as e:
        print(f"Error fetching shifts from Clover: {e}")
        return jsonify({"error": "Failed to fetch shifts"}), 500

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
                start_date,
                end_date,
                is_active,
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
        print("ERROR IN /api/employees:", e)
        return jsonify({"error": "Internal Server Error"}), 500


@app.route("/api/shifts", methods=["GET"])
def get_shifts():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT 
            s.id,
            s.shift_date,
            s.employee_id,
            e.preferred_name,
            s.time_in,
            s.time_out,
            s.work_area,
            s.shift_label,
            s.decimal_hours,
            s.notes
        FROM tbc.shifts s
        JOIN tbc.employees e ON s.employee_id = e.id
        ORDER BY s.shift_date DESC, s.time_in ASC;
    """)

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        shifts = []
        for row in rows:
            record = dict(zip(columns, row))
            # Convert time/date fields to string
            record["shift_date"] = record["shift_date"].isoformat() # this ensures shift_date → "2023-07-07"
            record["time_in"] = record["time_in"].isoformat() # this ensures time_in → "17:00:00"
            record["time_out"] = record["time_out"].isoformat() # this ensures time_out → "20:30:00"
            shifts.append(record)

        cursor.close()
        conn.close()

        return jsonify(shifts)

    except Exception as e:
        print("ERROR IN /api/shifts:", e)
        return jsonify({"error": "Internal Server Error"}), 500


# --- Main Entry ---
if __name__ == "__main__":
    app.run(debug=True)
