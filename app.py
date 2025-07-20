from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
import requests
from flask import request, jsonify
from datetime import datetime, timedelta
from psycopg2.extras import RealDictCursor
from urllib.parse import urlencode
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

@app.route('api/shifts-of-employee', methods=['GET'])
def get_shifts():
    clover_emp_id = 11
    clover_url = f"https://api.clover.com/v3/merchants/{CLOVER_MERCHANT_ID}/employees/{clover_emp_id}/shifts"
    headers = {
        "Authorization": f"Bearer {CLOVER_ACCESS_TOKEN}",
        "Accept": "application/json"
    }
    params = [
        ("expand", "employee"),
        ("filter", "has_in_time=true"),
        ("filter", "in_and_override_time>1747119600000"),
        ("filter", "in_and_override_time<1747634340000")
    ]
    
    # Log the full URL
    query_string = urlencode(params, doseq=True)
    full_url = f"{clover_url}?{query_string}"
    print(f"Sending request to: {full_url}")
    
    try:
        response = requests.get(clover_url, headers=headers, params=params)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        return jsonify({
            "status_code": response.status_code,
            "data": response.json()
        })
    except requests.RequestException as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/fetch-clover-shifts", methods=["POST"])
def fetch_clover_shifts():
    try:
        print("=== Clover Shift Fetch Started ===")
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get employee_id from request
        data = request.json
        employee_id = data.get("employee_id")
        if not employee_id:
            print("Missing employee_id in request")
            return jsonify({"error": "employee_id is required"}), 400
        print(f"Fetching shifts for employee_id: {employee_id}")

        # Step 1: Get Clover employee ID
        try:
            cursor.execute(
                "SELECT clover_employee_id FROM tbc.clover_employee_map WHERE employee_id = %s",
                (employee_id,))
            row = cursor.fetchone()
            if not row:
                print("No Clover mapping found for employee")
                return jsonify({"error": "Clover employee mapping not found"}), 404
            clover_emp_id = row["clover_employee_id"]
            print(f"Found Clover employee ID: {clover_emp_id}")
        except Exception as map_err:
            print("Error fetching Clover employee mapping:", map_err)
            raise

        # Step 2: Determine date range to fetch
        try:
            cursor.execute(
                "SELECT MAX(shift_date) FROM tbc.shifts_dummy_20250719 WHERE employee_id = %s",
                (employee_id,))
            max_date_row = cursor.fetchone()
            start_date = (max_date_row["max"] or datetime.today().date() - timedelta(days=7)) + timedelta(days=1)
            end_date = datetime.today().date()
            print(f"Fetching data from Clover between {start_date} and {end_date}")
        except Exception as date_err:
            print("Error determining date range:", date_err)
            raise

        # Convert to milliseconds for Clover API (start of day to end of today)
        start_ms = int(datetime.combine(start_date, datetime.min.time()).timestamp() * 1000)
        end_ms = int(datetime.combine(end_date, datetime.max.time()).timestamp() * 1000)

        # Step 3: Fetch from Clover
        clover_url = f"{CLOVER_BASE_URL}/v3/merchants/{CLOVER_MERCHANT_ID}/employees/{clover_emp_id}/shifts"
        headers = {
            "Authorization": f"Bearer {CLOVER_ACCESS_TOKEN}",
            "Accept": "application/json"
        }
        params = [
            ("expand", "employee"),
            ("filter", "has_in_time=true"),
            ("filter", f"in_and_override_time>{start_ms}"),
            ("filter", f"in_and_override_time<{end_ms}")
        ]
        print(f"Sending request to Clover API...\nURL: {clover_url}\nParams: {params}")
        response = requests.get(clover_url, headers=headers, params=params)
        
        if response.status_code != 200:
            print(f"Clover API failed with status {response.status_code}")
            print("Response:", response.text)
            return jsonify({"error": "Failed to fetch from Clover", "details": response.text}), 500

        clover_shifts = response.json().get("elements", [])
        print(f"Fetched {len(clover_shifts)} shifts from Clover")

        # Step 4: Prepare and insert shift data
        inserted_count = 0
        for shift in clover_shifts:
            try:
                in_ts = datetime.fromtimestamp(shift["inTime"] / 1000)
                out_ts = datetime.fromtimestamp(shift["outTime"] / 1000)
                shift_date = in_ts.date()
                decimal_hours = round((out_ts - in_ts).total_seconds() / 3600, 2)

                cursor.execute("""
                    INSERT INTO tbc.shifts_dummy_20250719 (
                        employee_id, shift_date, time_in, time_out, work_area, shift_label, decimal_hours, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    employee_id,
                    shift_date,
                    in_ts.time(),
                    out_ts.time(),
                    "unknown",  # default until manual update
                    determine_shift_label(in_ts.time()),
                    decimal_hours,
                    f"Clover ID: {shift.get('id')}"
                ))
                inserted_count += 1
            except Exception as insert_err:
                print(f"Failed to insert shift: {insert_err}")

        conn.commit()
        print(f"{inserted_count} new shifts inserted.")
        return jsonify({"status": "success", "inserted": inserted_count})
    except Exception as e:
        print("ERROR IN /api/fetch_clover_shifts:", e)
        return jsonify({"error": "Internal Server Error"}), 500

    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass

def determine_shift_label(time_obj):
    # Uses current logic from earlier conversation
    if time_obj < datetime.strptime("14:30", "%H:%M").time():
        return "Lunch"
    elif time_obj < datetime.strptime("17:00", "%H:%M").time():
        return "Break"
    else:
        return "Dinner"


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
        FROM tbc.shifts_dummy_20250719 s
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
