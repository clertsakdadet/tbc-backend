from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
import requests
from flask import request, jsonify
from datetime import datetime, timedelta
from psycopg2.extras import RealDictCursor
from urllib.parse import urlencode
import zoneinfo
from datetime import timezone # Use UTC for start_ms and end_ms
import os   
from utils import clover_time_handler

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

@app.route('/api/shifts-of-employee', methods=['GET'])
def get_shifts_of_employee():
    clover_emp_id = 'H776EYJH0M2FY'
    clover_url = f"https://api.clover.com/v3/merchants/{CLOVER_MERCHANT_ID}/employees/{clover_emp_id}/shifts"
    headers = {
        "Authorization": f"Bearer {CLOVER_ACCESS_TOKEN}",
        "Accept": "application/json"
    }
    params = [
        ("expand", "employee"),
        ("filter", "has_in_time=true"),
        ("filter", "in_and_override_time>1752288000000"), #July 12, 2025, 00:00:00 GMT.
        ("filter", "in_and_override_time<1752710400000") #July 15, 2025, 00:00:00 GMT.  
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
        
        # Step 2: Get employee role from tbc.employees
        try:
            cursor.execute(
                # IMPORTANT: Role (Front or Back) is used as work_area in Clover shifts
                "SELECT role FROM tbc.employees WHERE id = %s",
                (employee_id,))
            row = cursor.fetchone()
            if not row:
                print("No employee found for employee_id")
                return jsonify({"error": "Employee not found"}), 404
            work_area = row["role"]
            print(f"Employee role (work_area): {work_area}")
        except Exception as role_err:
            print("Error fetching employee role:", role_err)
            raise

        # Step 3: Determine date range to fetch
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

        # Convert to milliseconds for Clover API ('start of day' to 'end of today' ensuring the latest shifts are included)
        start_ms = clover_time_handler.readable_to_epoch(start_date.isoformat(), "start")
        end_ms = clover_time_handler.readable_to_epoch(end_date.isoformat(), "end")

        print(f"Fetching from PST {start_ms}")
        print(f"Fetching to PST {end_ms}")

        # Step 4: Fetch from Clover
        clover_url = f"{CLOVER_BASE_URL}/{CLOVER_MERCHANT_ID}/employees/{clover_emp_id}/shifts"
        headers = {
            "Authorization": f"Bearer {CLOVER_ACCESS_TOKEN}",
            "Accept": "application/json"
        }
        params = [
            ("limit", 1000),  # Make sure all possible records are returned [Default limit is 100 records]
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
        pacific = zoneinfo.ZoneInfo("America/Los_Angeles")
        preview_data = []
        for shift in clover_shifts:
            try:
                in_ms = shift.get("overrideInTime") or shift.get("inTime")
                out_ms = shift.get("overrideOutTime") or shift.get("outTime")
                in_ts = datetime.fromtimestamp(in_ms / 1000, tz=pacific)
                out_ts = datetime.fromtimestamp(out_ms / 1000, tz=pacific)
                shift_date = in_ts.date()
                time_in = in_ts.strftime("%H:%M:00") # Ensures time_in is in HH:MM:00 format
                time_out = out_ts.strftime("%H:%M:00") # Ensures time_out is in HH:MM:00 format
                
                # Calculate decimal_hours using only hours and minutes
                hours = out_ts.hour - in_ts.hour
                minutes = out_ts.minute - in_ts.minute
                decimal_hours = round(hours + minutes / 60, 2)
                shift_label = determine_shift_label(in_ts.time())

                preview_data.append({
                    "employee_id": employee_id,
                    "shift_date": str(shift_date),
                    "time_in": time_in,
                    "time_out": time_out,
                    "work_area": work_area,  # Use role from tbc.employees
                    "shift_label": shift_label,
                    "decimal_hours": decimal_hours,
                    "notes": ""
                })
            except Exception as parse_err:
                print("Failed to parse shift record:", parse_err)
        
        # Sort preview_data by shift_date in ascending order
        preview_data = sorted(preview_data, key=lambda x: datetime.strptime(x["shift_date"], "%Y-%m-%d").date())

        return jsonify({"status": "success", "preview": preview_data})

    except Exception as e:
        print("ERROR IN /api/fetch-clover-shifts:", e)
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


@app.route('/api/fetch-clover-shifts-bulk', methods=['POST'])
def fetch_clover_shifts_bulk():
    try:
        print("=== Bulk Clover Shift Fetch Started ===")
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Step 1: Get all active employees with Clover mapping
        cursor.execute("""
            SELECT e.id AS employee_id, cem.clover_employee_id, e.role, e.preferred_name
            FROM tbc.clover_employee_map cem
            JOIN tbc.employees e ON cem.employee_id = e.id
            WHERE e.is_active = TRUE
        """)
        employee_map = cursor.fetchall()
        pacific = zoneinfo.ZoneInfo("America/Los_Angeles")

        imported = 0
        skipped = 0
        errors = []

        for emp in employee_map:
            employee_id = emp["employee_id"]
            clover_emp_id = emp["clover_employee_id"]
            work_area = emp["role"]
            preferred_name = emp.get("preferred_name", "")
            
            print(f"Fetching shifts for employee_id: {employee_id} ({preferred_name})")

            # Step 2: Determine date range to fetch
            try:
                cursor.execute(
                    "SELECT MAX(shift_date) FROM tbc.shifts_dummy_20250719 WHERE employee_id = %s",
                    (employee_id,))
                max_date_row = cursor.fetchone()
                start_date = (max_date_row["max"] or datetime.today().date() - timedelta(days=7)) + timedelta(days=1)
                end_date = datetime.today().date()
                print(f"Fetching data from Clover between {start_date} and {end_date} for employee {employee_id}")
            except Exception as date_err:
                print("Error determining date range:", date_err)
                raise

            # Convert to milliseconds for Clover API
            start_ms = clover_time_handler.readable_to_epoch(start_date.isoformat(), "start")
            end_ms = clover_time_handler.readable_to_epoch(end_date.isoformat(), "end")

            # Step 3: Fetch from Clover
            clover_url = f"{CLOVER_BASE_URL}/{CLOVER_MERCHANT_ID}/employees/{clover_emp_id}/shifts"
            headers = {
                "Authorization": f"Bearer {CLOVER_ACCESS_TOKEN}",
                "Accept": "application/json"
            }
            params = [
                ("limit", 1000),  # Make sure all possible records are returned [Default limit is 100 records]
                ("expand", "employee"),
                ("filter", "has_in_time=true"),
                ("filter", f"in_and_override_time>{start_ms}"),
                ("filter", f"in_and_override_time<{end_ms}")
            ]
            try:
                response = requests.get(clover_url, headers=headers, params=params)
                if response.status_code != 200:
                    print(f"Failed for employee {employee_id}: {response.status_code}")
                    errors.append({"employee_id": employee_id, "error": response.text})
                    continue

                clover_shifts = response.json().get("elements", [])
                for shift in clover_shifts:
                    try:
                        in_ms = shift.get("overrideInTime") or shift.get("inTime")
                        out_ms = shift.get("overrideOutTime") or shift.get("outTime")
                        if not in_ms or not out_ms:
                            skipped += 1
                            continue
                        in_ts = datetime.fromtimestamp(in_ms / 1000, tz=pacific)
                        out_ts = datetime.fromtimestamp(out_ms / 1000, tz=pacific)
                        shift_date = in_ts.date()
                        time_in = in_ts.strftime("%H:%M:00")
                        time_out = out_ts.strftime("%H:%M:00")
                        hours = out_ts.hour - in_ts.hour
                        minutes = out_ts.minute - in_ts.minute
                        decimal_hours = round(hours + minutes / 60, 2)
                        shift_label = determine_shift_label(in_ts.time())
                        clover_shift_id = shift.get("id")

                        # Insert into staged_shifts, avoid duplicates
                        insert_query = """
                            INSERT INTO tbc.staged_shifts (
                                employee_id, clover_shift_id, shift_date, time_in, time_out,
                                work_area, shift_label, decimal_hours
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (employee_id, shift_date, time_in, time_out, clover_shift_id) DO NOTHING
                        """
                        cursor.execute(insert_query, (
                            employee_id,
                            clover_shift_id,
                            shift_date,
                            time_in,
                            time_out,
                            work_area,
                            shift_label,
                            decimal_hours
                        ))
                        if cursor.rowcount == 1:
                            imported += 1
                        else:
                            skipped += 1
                    except Exception as parse_err:
                        print(f"Failed to parse/insert shift for employee {employee_id}: {parse_err}")
                        errors.append({"employee_id": employee_id, "error": str(parse_err)})
                        skipped += 1
                conn.commit()
            except Exception as e:
                print(f"Error fetching/inserting for employee {employee_id}: {e}")
                errors.append({"employee_id": employee_id, "error": str(e)})
                continue

        # --- Fetch all staged shifts that have not been promoted ---
        cursor.execute("""
            SELECT 
                ss.id,
                ss.employee_id,
                e.preferred_name,
                ss.clover_shift_id,
                ss.shift_date,
                ss.time_in,
                ss.time_out,
                ss.work_area,
                ss.shift_label,
                ss.decimal_hours,
                ss.notes
            FROM tbc.staged_shifts ss
            JOIN tbc.employees e ON ss.employee_id = e.id
            WHERE ss.is_promoted = FALSE
            ORDER BY ss.shift_date ASC, ss.time_in ASC
        """)
        preview_data = cursor.fetchall()

        # Convert date/time fields to string for JSON serialization
        for row in preview_data:
            if isinstance(row["shift_date"], (datetime, )):
                row["shift_date"] = row["shift_date"].date().isoformat()
            elif hasattr(row["shift_date"], "isoformat"):
                row["shift_date"] = row["shift_date"].isoformat()
            if "time_in" in row and hasattr(row["time_in"], "isoformat"):
                row["time_in"] = row["time_in"].isoformat()
            if "time_out" in row and hasattr(row["time_out"], "isoformat"):
                row["time_out"] = row["time_out"].isoformat()

        # Sort preview_data by shift_date in ascending order
        preview_data = sorted(
            preview_data,
            key=lambda x: datetime.strptime(x["shift_date"], "%Y-%m-%d").date()
        )

        return jsonify({
            "status": "success",
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "preview": preview_data
        })

    except Exception as e:
        print("ERROR IN /api/fetch-clover-shifts-bulk:", e)
        return jsonify({"error": "Internal Server Error"}), 500

    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass

@app.route("/api/submit-clover-shifts", methods=["POST"])
def submit_clover_shifts():
    try:
        print("=== Submitting Clover Shifts ===")
        conn = get_db_connection()
        cursor = conn.cursor()

        data = request.json
        shifts = data.get("shifts")

        if not shifts:
            return jsonify({"error": "No shift data provided"}), 400

        insert_query = """
            INSERT INTO tbc.shifts_dummy_20250719 (
                employee_id, shift_date, time_in, time_out, work_area,
                shift_label, decimal_hours, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """

        promoted_ids = []
        for shift in shifts:
            cursor.execute(insert_query, (
                shift["employee_id"],
                shift["shift_date"],
                shift["time_in"],
                shift["time_out"],
                shift["work_area"],
                shift["shift_label"],
                shift["decimal_hours"],
                shift["notes"]
            ))
            # Collect staged shift id for promotion if present
            if "id" in shift:
                promoted_ids.append(shift["id"])

        # Mark corresponding staged_shifts as promoted
        if promoted_ids:
            cursor.execute(
                f"UPDATE tbc.staged_shifts SET is_promoted = TRUE WHERE id = ANY(%s)",
                (promoted_ids,)
            )

        conn.commit()
        return jsonify({"status": "success", "message": f"Inserted {len(shifts)} shifts and promoted {len(promoted_ids)} staged shifts."})

    except Exception as e:
        print("ERROR IN /api/submit_clover_shifts:", e)
        return jsonify({"error": "Failed to insert shifts"}), 500

    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass

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
