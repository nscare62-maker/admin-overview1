"""
Admin Dashboard for S2C Employee Monitoring System
Flask-based web application for managing employees, attendance, permissions, and sessions
"""

# Import necessary libraries
import sys
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from functools import wraps
import firebase_admin
from firebase_admin import credentials, db
import os
import base64
from dotenv import load_dotenv
from datetime import datetime, timedelta
import json
from filters import register_filters

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY') or os.getenv('SECRET_KEY', 's2c-admin-secret-key-change-in-production-2026')
app.config['AZURE_MAPS_SUBSCRIPTION_KEY'] = os.getenv('AZURE_MAPS_SUBSCRIPTION_KEY', '')



def get_employee_initials(name):
    """Extract two uppercase initials from employee name for avatar display."""
    if not name or name == 'Unknown':
        return 'EM'
    parts = str(name).strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    cleaned = str(name).strip()
    return cleaned[:2].upper() if len(cleaned) >= 2 else (cleaned + 'X').upper()

def safe_num(val, default=0):
    """Safely convert any value to int or float, returning default if invalid or None."""
    if val in (None, '', 'N/A', 'null', 'None'):
        return default
    try:
        if isinstance(val, (int, float)):
            return val
        s = str(val).strip()
        if '.' in s:
            return float(s)
        return int(s)
    except (ValueError, TypeError):
        return default


def hash_password(password):
    """
    Hash password using the same algorithm as the mobile app:
    - Salt: V_TRACE_APP_SALT_2024
    - Method: Base64 encoding 6 times
    """
    salt = "V_TRACE_APP_SALT_2024"
    combined = salt + password + salt
    
    hashed = base64.b64encode(combined.encode('utf-8')).decode('utf-8')
    
    for _ in range(5):
        hashed = base64.b64encode(hashed.encode('utf-8')).decode('utf-8')
    
    return hashed

def verify_password(password, stored_hash):
    """Verify password against stored hash"""
    computed = hash_password(password)
    return computed == stored_hash


# Register custom Jinja2 filters
register_filters(app)

# Add datetime to Jinja2 context
@app.context_processor
def inject_now():
    return {'now': datetime.now}

# Helper function to get employee by employeeId
def get_employee_by_id(all_employees, employee_id):
    """Find employee by employeeId, phone, or internal phone-key match."""
    if not employee_id:
        return {}

    employee_id = str(employee_id)

    for phone, emp_data in all_employees.items():
        if not isinstance(emp_data, dict):
            continue

        if (
            emp_data.get('employeeId') == employee_id or
            emp_data.get('phone') == employee_id or
            emp_data.get('empPhone') == employee_id or
            phone == employee_id
        ):
            return emp_data

    return {}


def get_employee_reference(employee_data):
    """Return the employee reference stored in the most common Firebase field names."""
    if not isinstance(employee_data, dict):
        return ''

    for field in ('empPhone', 'employeePhone', 'phone', 'employeeId'):
        value = employee_data.get(field)
        if value not in (None, '', 'N/A'):
            return str(value)

    return ''


def get_employee_filter_candidates(all_employees, employee_filter):
    """Return all employee identifiers linked to the selected employee filter value."""
    if employee_filter in (None, '', 'all'):
        return set()

    employee_filter = str(employee_filter).strip()
    candidates = {employee_filter}

    for phone, emp_data in all_employees.items():
        if not isinstance(emp_data, dict):
            continue

        emp_identifiers = {
            str(emp_data.get('employeeId', '')).strip(),
            str(emp_data.get('empPhone', '')).strip(),
            str(emp_data.get('employeePhone', '')).strip(),
            str(emp_data.get('phone', '')).strip(),
            str(phone).strip(),
            str(emp_data.get('name', '')).strip(),
        }
        emp_identifiers.discard('')
        emp_identifiers.discard('None')
        emp_identifiers.discard('N/A')

        if employee_filter in emp_identifiers:
            candidates.update(emp_identifiers)
            break

    return {item for item in candidates if item not in (None, '', 'N/A', 'None')}


def get_employee_identifier_candidates(employee_data):
    """Return all common employee identifiers stored on a Firebase record."""
    if not isinstance(employee_data, dict):
        return set()

    candidates = set()
    for field in ('employeeId', 'empPhone', 'employeePhone', 'phone', 'employeeName', 'name', 'userId'):
        value = employee_data.get(field)
        if value not in (None, '', 'N/A', 'null', 'None'):
            candidates.add(str(value).strip())

    return candidates

def load_session_photo(session_id, photo_type, session_data=None):
    """Resolve a session photo from its URI or the session_photos collection."""
    photo_uri = (session_data or {}).get(f'{photo_type}PhotoUri', '')
    if photo_uri in ('auto-punched-out', 'auto-closed-extended-break',
                     'auto-closed-evening', ''):
        photo_uri = ''

    def normalize_photo(value):
        if isinstance(value, dict):
            value = (value.get('imageBase64') or value.get('imageUrl') or
                     value.get('downloadUrl') or value.get('url'))
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        if value.startswith(('/data/', 'file://', 'content://')):
            return None
        if value.startswith('data:image') or value.startswith(('http://', 'https://', '/')):
            return value
        if len(value) < 100:
            return None
        return f'data:image/jpeg;base64,{value}'

    if photo_uri:
        if photo_uri.startswith('session_photos/'):
            try:
                photo_data = db.reference(photo_uri).get()
                if photo_data:
                    photo = normalize_photo(photo_data)
                    if photo:
                        return photo
            except Exception:
                pass
            return None

        # Check if photo_uri is a direct push key in session_photos
        if photo_uri.startswith('-'):
            try:
                photo_data = db.reference(f'session_photos/{photo_uri}').get()
                if photo_data:
                    photo = normalize_photo(photo_data)
                    if photo:
                        return photo
            except Exception:
                pass

        photo = normalize_photo(photo_uri)
        if photo:
            return photo

    # Also check if session has direct photo field
    direct_photo = (session_data or {}).get(f'{photo_type}Photo')
    if direct_photo:
        photo = normalize_photo(direct_photo)
        if photo:
            return photo

    # Also check candidate direct keys under session_photos
    if session_id:
        for candidate_key in (f'{session_id}_{photo_type}', session_id):
            try:
                candidate_data = db.reference(f'session_photos/{candidate_key}').get()
                if candidate_data:
                    photo = normalize_photo(candidate_data)
                    if photo:
                        return photo
            except Exception:
                pass

    return None

def has_stored_photo(value):
    """Return whether a photo field contains a browser-usable image reference."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value or value in ('auto-punched-out', 'auto-closed-extended-break',
                              'auto-closed-evening'):
        return False
    if value.startswith(('/data/', 'file://', 'content://')):
        return False
    return (value.startswith(('data:image', 'http://', 'https://',
                              'session_photos/')) or value.startswith('-') or len(value) >= 100)


def normalize_visit_datetime(value):
    """Convert visit timestamp or createdAt to sortable datetime and display strings."""
    if value in (None, '', 'N/A', 'null', 'None', '0'):
        return {
            'sort_key': 0,
            'date': 'N/A',
            'time': 'N/A',
            'datetime': 'N/A'
        }

    dt = None

    try:
        if isinstance(value, (int, float)):
            numeric_value = float(value)
            if numeric_value > 1_000_000_000_000:
                numeric_value /= 1000.0
            dt = datetime.fromtimestamp(numeric_value)
        elif isinstance(value, str):
            raw_value = value.strip()
            if raw_value.isdigit() or (raw_value.startswith('-') and raw_value[1:].isdigit()):
                numeric_value = float(raw_value)
                if abs(numeric_value) > 1_000_000_000_000:
                    numeric_value /= 1000.0
                dt = datetime.fromtimestamp(numeric_value)
            else:
                for fmt in (
                    '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%d %H:%M',
                    '%Y-%m-%dT%H:%M:%S',
                    '%Y-%m-%dT%H:%M:%S.%f',
                    '%Y-%m-%d'
                ):
                    try:
                        dt = datetime.strptime(raw_value, fmt)
                        break
                    except ValueError:
                        pass

                if dt is None:
                    try:
                        dt = datetime.fromisoformat(raw_value.replace('Z', '+00:00'))
                    except ValueError:
                        pass
    except Exception:
        dt = None

    if dt is None:
        return {
            'sort_key': 0,
            'date': 'N/A',
            'time': 'N/A',
            'datetime': 'N/A'
        }

    return {
        'sort_key': dt.timestamp(),
        'date': dt.strftime('%Y-%m-%d'),
        'time': dt.strftime('%I:%M %p'),
        'datetime': dt.strftime('%Y-%m-%d %I:%M %p')
    }

# Initialize Firebase
try:
    cred_json_env = os.getenv('FIREBASE_CREDENTIALS_JSON')
    if cred_json_env:
        import json
        cred_dict = json.loads(cred_json_env)
        cred = credentials.Certificate(cred_dict)
    else:
        # Resolve credentials path relative to this script's directory
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'firebase-credentials.json')
        if not os.path.isabs(cred_path):
            cred_path = os.path.join(_script_dir, cred_path)

        if not os.path.exists(cred_path):
            raise FileNotFoundError(f"Firebase credentials file not found at: {cred_path}")

        cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {
        'databaseURL': os.getenv('FIREBASE_DATABASE_URL', 'https://login-otp-29372-default-rtdb.firebaseio.com')
    })
    print("[INFO] Firebase initialized successfully")
except Exception as e:
    print(f"[ERROR] Firebase initialization error: {e}")
    raise

# Admin credentials (in production, use database with hashed passwords)
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')

_firebase_cache = {}
_firebase_cache_ttl = 30


def read_firebase_path(path):
    now = datetime.now().timestamp()
    cached = _firebase_cache.get(path)
    if cached and now - cached[0] < _firebase_cache_ttl:
        return cached[1]
    raw_val = db.reference(path).get()
    if raw_val is None:
        value = {}
    elif isinstance(raw_val, dict):
        value = raw_val
    elif isinstance(raw_val, list):
        value = {str(i): v for i, v in enumerate(raw_val) if v is not None}
    else:
        value = raw_val
    _firebase_cache[path] = (now, value)
    return value


def get_session_locations(session_id):
    """Return only the location records linked to the requested session."""
    if not session_id:
        return {}

    try:
        locations_ref = db.reference('locations')
        res = locations_ref.order_by_child('sessionId').equal_to(str(session_id)).get()
        if isinstance(res, dict):
            return res
        elif isinstance(res, list):
            return {str(i): loc for i, loc in enumerate(res) if isinstance(loc, dict)}
    except Exception:
        pass

    try:
        all_locations = read_firebase_path('locations')
        if isinstance(all_locations, dict):
            return {
                loc_id: location
                for loc_id, location in all_locations.items()
                if isinstance(location, dict) and str(location.get('sessionId')) == str(session_id)
            }
        elif isinstance(all_locations, list):
            return {
                str(i): location
                for i, location in enumerate(all_locations)
                if isinstance(location, dict) and str(location.get('sessionId')) == str(session_id)
            }
    except Exception:
        pass
    return {}


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function



@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials!', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('login'))



@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard with overview statistics"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        employees = read_firebase_path('users')
        if not isinstance(employees, dict):
            employees = {}
        
        field_staff = sum(1 for e in employees.values() if isinstance(e, dict) and e.get('role') == 'field_staff')
        office_staff = sum(1 for e in employees.values() if isinstance(e, dict) and e.get('role') == 'office_staff')
        
        sessions = read_firebase_path('sessions')
        if not isinstance(sessions, dict):
            sessions = {}
        active_sessions = [s for s in sessions.values() if isinstance(s, dict) and str(s.get('status', '')).upper() == 'ACTIVE']
        
        today_attendance = read_firebase_path(f'attendanceByDate/{today}')
        if not isinstance(today_attendance, dict):
            today_attendance = {}
        
        permissions = read_firebase_path('permissions')
        if not isinstance(permissions, dict):
            permissions = {}
        pending_permissions = [p for p in permissions.values() if isinstance(p, dict) and str(p.get('status', '')).upper() == 'PENDING']
        
        tasks = read_firebase_path('tasks')
        if not isinstance(tasks, dict):
            tasks = {}
        pending_tasks = sum(1 for t in tasks.values() if isinstance(t, dict) and str(t.get('status', '')).upper() == 'PENDING')
        
        visits = read_firebase_path('visits')
        if not isinstance(visits, dict):
            visits = {}
        today_visits = sum(1 for v in visits.values() if isinstance(v, dict) and v.get('date') == today)
        
        stats = {
            'total_employees': len(employees),
            'field_staff': field_staff,
            'office_staff': office_staff,
            'active_sessions': len(active_sessions),
            'today_attendance': len(today_attendance),
            'pending_permissions': len(pending_permissions),
            'total_sessions': len(sessions),
            'pending_tasks': pending_tasks,
            'total_tasks': len(tasks),
            'today_visits': today_visits,
            'total_visits': len(visits)
        }
        
        return render_template('dashboard.html', stats=stats)
    except Exception as e:
        flash(f'Error loading dashboard: {str(e)}', 'error')
        return render_template('dashboard.html', stats={})

# ═══════════════════════════════════════════════════════════════════════════
# EMPLOYEE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/employees')
@login_required
def employees():
    """List all employees"""
    try:
        # Employees are stored under 'users' in Firebase
        employees_ref = db.reference('users')
        employees_data = employees_ref.get() or {}
        
        employees_list = []
        for emp_id, emp_data in employees_data.items():
            employees_list.append({
                'id': emp_id,
                'name': emp_data.get('name', 'N/A'),
                'phone': emp_data.get('phone', 'N/A'),
                'role': emp_data.get('role', 'field_staff'),
                'employeeId': emp_data.get('employeeId', emp_id),
                'status': 'Active'
            })
        
        return render_template('employees.html', employees=employees_list)
    except Exception as e:
        flash(f'Error loading employees: {str(e)}', 'error')
        return render_template('employees.html', employees=[])

@app.route('/employees/<employee_id>')
@login_required
def employee_detail(employee_id):
    """View employee details"""
    try:
        # Get employee data from 'users' node
        emp_ref = db.reference(f'users/{employee_id}')
        employee = emp_ref.get()
        
        if not employee:
            flash('Employee not found!', 'error')
            return redirect(url_for('employees'))
        
        # Get employee's recent sessions - fetch all and filter locally
        sessions_ref = db.reference('sessions')
        all_sessions = sessions_ref.get() or {}
        
        # Filter sessions for this employee
        employee_sessions = []
        for sess_id, sess_data in all_sessions.items():
            if sess_data.get('employeeId') == employee_id:
                employee_sessions.append({
                    'id': sess_id,
                    'startTime': sess_data.get('startTime', 0),
                    'endTime': sess_data.get('endTime'),
                    'status': sess_data.get('status'),
                    'workType': sess_data.get('workType'),
                    'totalWorkTimeMs': sess_data.get('totalWorkTimeMs', 0),
                    'totalBreakTimeMs': sess_data.get('totalBreakTimeMs', 0)
                })
        
        # Sort by start time and get last 10
        employee_sessions.sort(key=lambda x: safe_num(x.get('startTime')), reverse=True)
        sessions_list = employee_sessions[:10]
        
        # Get employee's attendance - fetch all and filter locally
        attendance_ref = db.reference('attendance')
        all_attendance = attendance_ref.get() or {}
        
        # Filter attendance for this employee
        employee_attendance = []
        for att_id, att_data in all_attendance.items():
            if isinstance(att_data, dict) and att_data.get('employeeId') == employee_id:
                employee_attendance.append({
                    'id': att_id,
                    'date': att_data.get('date'),
                    'startTime': att_data.get('startTime', 0),
                    'status': att_data.get('status'),
                    'lateByMinutes': att_data.get('lateByMinutes', 0),
                    'isHalfDay': att_data.get('isHalfDay', False)
                })
        
        # Sort by start time and get last 10
        employee_attendance.sort(key=lambda x: safe_num(x.get('startTime')), reverse=True)
        attendance_list = employee_attendance[:10]
        
        # Get monthly summary
        current_month = datetime.now().strftime('%Y-%m')
        summary_ref = db.reference(f'attendanceSummary/{employee_id}/{current_month}')
        monthly_summary = summary_ref.get()
        
        employee['id'] = employee_id
        
        return render_template('employee_detail.html', 
                             employee=employee, 
                             sessions=sessions_list,
                             attendance=attendance_list,
                             monthly_summary=monthly_summary)
    except Exception as e:
        flash(f'Error loading employee details: {str(e)}', 'error')
        return redirect(url_for('employees'))

@app.route('/employees/add', methods=['GET', 'POST'])
@login_required
def add_employee():
    """Add new employee"""
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            phone = request.form.get('phone', '').strip()
            password = request.form.get('password', '').strip()
            
            # Validation
            if not name or not phone or not password:
                flash('Name, Phone, and Password are required!', 'danger')
                return render_template('add_employee.html')
            
            # Validate phone number format (10 digits)
            if not phone.isdigit() or len(phone) != 10:
                flash('Phone number must be exactly 10 digits!', 'danger')
                return render_template('add_employee.html')
            
            # Check if phone already exists
            users_ref = db.reference('users')
            existing_users = users_ref.get() or {}
            
            if phone in existing_users:
                flash(f'User with phone {phone} already exists!', 'danger')
                return render_template('add_employee.html')
            
            # Hash the password
            hashed_password = hash_password(password)
            
            # Create new user
            user_data = {
                'name': name,
                'phone': phone,
                'password': hashed_password,  # Store hashed password
                'employeeId': phone,  # Use phone as employeeId by default
                'role': 'field_staff',  # Default role
                'createdAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'createdBy': session.get('username', 'admin')
            }
            
            # Save to Firebase (phone is the key)
            users_ref.child(phone).set(user_data)
            
            flash(f'User {name} added successfully! Phone: {phone}', 'success')
            return redirect(url_for('employees'))
            
        except Exception as e:
            flash(f'Error adding user: {str(e)}', 'danger')
            return render_template('add_employee.html')
    
    # GET request - show form
    return render_template('add_employee.html')

@app.route('/employees/<employee_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_employee(employee_id):
    """Edit employee name and employeeId without affecting attendance records"""
    emp_ref = db.reference(f'users/{employee_id}')
    employee = emp_ref.get()

    if not employee:
        flash('Employee not found!', 'danger')
        return redirect(url_for('employees'))

    if request.method == 'POST':
        try:
            new_name = request.form.get('name', '').strip()
            new_employee_id = request.form.get('employeeId', '').strip()

            if not new_name:
                flash('Name cannot be empty!', 'danger')
                employee['id'] = employee_id
                return render_template('edit_employee.html', employee=employee)

            if not new_employee_id:
                flash('Employee ID cannot be empty!', 'danger')
                employee['id'] = employee_id
                return render_template('edit_employee.html', employee=employee)

            # Check if new employeeId is already taken by another user
            if new_employee_id != employee.get('employeeId'):
                all_users = db.reference('users').get() or {}
                for uid, udata in all_users.items():
                    if uid != employee_id and udata.get('employeeId') == new_employee_id:
                        flash(f'Employee ID "{new_employee_id}" is already in use!', 'danger')
                        employee['id'] = employee_id
                        return render_template('edit_employee.html', employee=employee)

            emp_ref.update({
                'name': new_name,
                'employeeId': new_employee_id,
                'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'updatedBy': session.get('username', 'admin')
            })

            flash(f'Employee updated successfully!', 'success')
            return redirect(url_for('employee_detail', employee_id=employee_id))

        except Exception as e:
            flash(f'Error updating employee: {str(e)}', 'danger')

    employee['id'] = employee_id
    return render_template('edit_employee.html', employee=employee)


@app.route('/employees/<employee_id>/reset-password', methods=['POST'])
@login_required
def reset_password(employee_id):
    """Reset employee password"""
    try:
        new_password = request.form.get('new_password', '').strip()
        
        if not new_password:
            flash('Password cannot be empty!', 'danger')
            return redirect(url_for('employee_detail', employee_id=employee_id))
        
        if len(new_password) < 4:
            flash('Password must be at least 4 characters!', 'danger')
            return redirect(url_for('employee_detail', employee_id=employee_id))
        
        # Update password in Firebase
        users_ref = db.reference(f'users/{employee_id}')
        user = users_ref.get()
        
        if not user:
            flash('User not found!', 'danger')
            return redirect(url_for('employees'))
        
        # Hash the new password
        hashed_password = hash_password(new_password)
        
        users_ref.update({
            'password': hashed_password,  # Store hashed password
            'passwordUpdatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'passwordUpdatedBy': session.get('username', 'admin')
        })
        
        flash(f'Password reset successfully for {user.get("name", "user")}!', 'success')
        return redirect(url_for('employee_detail', employee_id=employee_id))
        
    except Exception as e:
        flash(f'Error resetting password: {str(e)}', 'danger')
        return redirect(url_for('employee_detail', employee_id=employee_id))

# ═══════════════════════════════════════════════════════════════════════════
# ATTENDANCE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/attendance')
@login_required
def attendance():
    """View all attendance records with start and end times"""
    try:
        date_filter = request.args.get('date', '').strip() or 'all'
        status_filter = request.args.get('status', '').strip() or 'all'
        employee_filter = request.args.get('employee', '').strip() or 'all'
        
        all_attendance = read_firebase_path('attendance')
        all_sessions = read_firebase_path('sessions')
        all_employees = read_firebase_path('users')
        
        attendance_list = []
        for att_id, att_data in all_attendance.items():
            if not isinstance(att_data, dict):
                continue

            att_date = att_data.get('date')
            if not att_date or str(att_date).strip() in ('N/A', 'None', 'null', ''):
                st = att_data.get('startTime')
                if st:
                    try:
                        att_date = datetime.fromtimestamp(int(st) / 1000).strftime('%Y-%m-%d')
                    except Exception:
                        att_date = 'N/A'
                else:
                    att_date = 'N/A'

            if date_filter != 'all' and att_date != date_filter:
                continue

            att_status = str(att_data.get('status', '')).upper()
            if status_filter != 'all' and att_status != status_filter.upper():
                continue

            if employee_filter != 'all':
                filter_candidates = get_employee_filter_candidates(all_employees, employee_filter)
                record_candidates = get_employee_identifier_candidates(att_data)
                if not record_candidates.intersection(filter_candidates):
                    continue

            # Find corresponding session to get endTime
            session_id = att_data.get('sessionId')
            end_time = None
            work_duration_hours = 0
            
            if session_id and session_id in all_sessions:
                session = all_sessions[session_id]
                if isinstance(session, dict):
                    end_time = session.get('endTime')
                    
                    start_time = att_data.get('startTime', 0)
                    if start_time and end_time:
                        try:
                            duration_ms = int(end_time) - int(start_time)
                            work_duration_hours = round(duration_ms / (1000 * 60 * 60), 2)
                        except Exception:
                            pass
            
            attendance_list.append({
                'id': att_id,
                'employeeId': att_data.get('employeeId'),
                'employeeName': att_data.get('employeeName') or 'Unknown',
                'date': att_date,
                'startTime': att_data.get('startTime'),
                'endTime': end_time,
                'workDurationHours': work_duration_hours,
                'status': att_data.get('status'),
                'lateByMinutes': att_data.get('lateByMinutes', 0),
                'isHalfDay': att_data.get('isHalfDay', False),
                'approvalStatus': att_data.get('approvalStatus', 'PENDING')
            })
        
        attendance_list.sort(key=lambda x: safe_num(x.get('startTime')), reverse=True)

        employee_list = []
        seen_emp_ids = set()
        for emp_id, emp_data in all_employees.items():
            if not isinstance(emp_data, dict):
                continue
            employee_ref = (
                emp_data.get('employeePhone') or
                emp_data.get('empPhone') or
                emp_data.get('phone') or
                emp_data.get('employeeId') or
                emp_id
            )
            name = emp_data.get('name') or 'Unknown'
            seen_emp_ids.add(str(employee_ref))
            if emp_data.get('employeeId'):
                seen_emp_ids.add(str(emp_data.get('employeeId')))
            employee_list.append({
                'id': employee_ref,
                'name': name
            })
        for att_data in all_attendance.values():
            if not isinstance(att_data, dict):
                continue
            att_emp_id = att_data.get('employeeId')
            if att_emp_id and str(att_emp_id) not in seen_emp_ids:
                seen_emp_ids.add(str(att_emp_id))
                employee_list.append({
                    'id': str(att_emp_id),
                    'name': att_data.get('employeeName') or str(att_emp_id)
                })
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('attendance.html', 
                             attendance=attendance_list, 
                             date_filter=date_filter,
                             status_filter=status_filter,
                             employee_filter=employee_filter,
                             employees=employee_list)
    except Exception as e:
        flash(f'Error loading attendance: {str(e)}', 'error')
        return render_template('attendance.html', attendance=[], employees=[])

@app.route('/attendance/<record_id>/approve', methods=['POST'])
@login_required
def approve_attendance(record_id):
    """Approve attendance record"""
    try:
        remarks = request.form.get('remarks', '')
        
        att_ref = db.reference(f'attendance/{record_id}')
        att_ref.update({
            'approvalStatus': 'APPROVED',
            'approvedBy': session.get('username', 'Admin'),
            'approverRemarks': remarks
        })
        
        flash('Attendance approved successfully!', 'success')
    except Exception as e:
        flash(f'Error approving attendance: {str(e)}', 'error')
    
    return redirect(url_for('attendance'))

@app.route('/attendance/<record_id>/reject', methods=['POST'])
@login_required
def reject_attendance(record_id):
    """Reject attendance record"""
    try:
        remarks = request.form.get('remarks', 'Rejected by admin')
        
        att_ref = db.reference(f'attendance/{record_id}')
        att_ref.update({
            'approvalStatus': 'REJECTED',
            'approvedBy': session.get('username', 'Admin'),
            'approverRemarks': remarks
        })
        
        flash('Attendance rejected!', 'warning')
    except Exception as e:
        flash(f'Error rejecting attendance: {str(e)}', 'error')
    
    return redirect(url_for('attendance'))

# ═══════════════════════════════════════════════════════════════════════════
# PERMISSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/permissions')
@login_required
def permissions():
    """View all permission requests"""
    try:
        status_filter = request.args.get('status', '').strip().upper() or 'PENDING'
        if status_filter in ('', 'ALL'):
            status_filter = 'all'
        
        permissions_ref = db.reference('permissions')
        all_permissions = permissions_ref.get() or {}
        all_leaves = db.reference('leaves').get() or {}
        
        permissions_list = []
        combined_permissions = list(all_permissions.items()) + list(all_leaves.items())
        seen_requests = set()
        for perm_id, perm_data in combined_permissions:
            permission_status = str(perm_data.get('status', 'PENDING')).upper()
            reason = perm_data.get('reason') or perm_data.get('purpose', '')
            start_date = perm_data.get('startDate') or perm_data.get('fromDate') or perm_data.get('date')
            request_key = (perm_data.get('employeeId'), reason.strip(), str(start_date)[:10])
            if request_key in seen_requests:
                continue
            if status_filter == 'all' or permission_status == status_filter.upper():
                seen_requests.add(request_key)
                permissions_list.append({
                    'id': perm_id,
                    'employeeId': perm_data.get('employeeId'),
                    'employeeName': perm_data.get('employeeName'),
                    'type': perm_data.get('type', 'PERMISSION'),
                    'status': permission_status,
                    'startDate': start_date,
                    'endDate': perm_data.get('endDate') or perm_data.get('toDate') or perm_data.get('date'),
                    'duration': perm_data.get('duration') or perm_data.get('dayCount', 0),
                    'durationDisplay': (
                        f"{perm_data.get('dayCount')} day(s)"
                        if perm_data.get('dayCount') is not None
                        else str(perm_data.get('duration', 0))
                    ),
                    'reason': reason,
                    'requestedAt': perm_data.get('requestedAt') or perm_data.get('createdAt') or 0
                })
        
        def permission_sort_key(permission):
            value = permission.get('requestedAt', 0)
            try:
                if isinstance(value, str) and not value.isdigit():
                    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
                return float(value or 0)
            except (TypeError, ValueError, OverflowError):
                return 0

        permissions_list.sort(key=permission_sort_key, reverse=True)
        
        return render_template('permissions.html', 
                             permissions=permissions_list,
                             status_filter=status_filter)
    except Exception as e:
        flash(f'Error loading permissions: {str(e)}', 'error')
        return render_template('permissions.html', permissions=[])

@app.route('/permissions/<request_id>/approve', methods=['POST'])
@login_required
def approve_permission(request_id):
    """Approve permission request"""
    try:
        remarks = request.form.get('remarks', '')
        
        perm_ref = db.reference(f'permissions/{request_id}')
        perm_data = perm_ref.get()
        is_leave_record = not perm_data
        if is_leave_record:
            perm_ref = db.reference(f'leaves/{request_id}')
            perm_data = perm_ref.get()
        if not perm_data:
            flash('Permission request not found!', 'error')
            return redirect(url_for('permissions'))
        
        perm_ref.update({
            'status': 'approved' if is_leave_record else 'APPROVED',
            'approvedBy': session.get('username', 'Admin'),
            'approvedAt': int(datetime.now().timestamp() * 1000),
            'approverRemarks': remarks
        })
        
        # If late start, update the late start record
        if str(perm_data.get('type', '')).upper() == 'LATE_START':
            emp_id = perm_data.get('employeeId')
            start_date = perm_data.get('startDate', 0)
            try:
                # Handle both string and int timestamps
                if isinstance(start_date, str):
                    start_date = int(start_date)
                month = datetime.fromtimestamp(start_date / 1000).strftime('%Y-%m')
                year = datetime.fromtimestamp(start_date / 1000).year
            except:
                month = datetime.now().strftime('%Y-%m')
                year = datetime.now().year
            
            # Update late start record
            late_ref = db.reference(f'lateStartRecords/{emp_id}/{month}')
            late_data = late_ref.get() or {'count': 0, 'totalMinutes': 0}
            if not isinstance(late_data, dict):
                late_data = {'count': 0, 'totalMinutes': 0}
            
            curr_count = safe_num(late_data.get('count'), 0)
            curr_mins = safe_num(late_data.get('totalMinutes'), 0)
            perm_dur = safe_num(perm_data.get('duration'), 0)
            
            late_ref.update({
                'employeeId': emp_id,
                'month': month,
                'year': year,
                'count': curr_count + 1,
                'totalMinutes': curr_mins + perm_dur,
                'halfDayCut': (curr_count + 1) > 2
            })
        
        flash('Permission approved successfully!', 'success')
    except Exception as e:
        flash(f'Error approving permission: {str(e)}', 'error')
    
    return redirect(url_for('permissions'))

@app.route('/permissions/<request_id>/reject', methods=['POST'])
@login_required
def reject_permission(request_id):
    """Reject permission request"""
    try:
        remarks = request.form.get('remarks', 'Rejected by admin')
        
        perm_ref = db.reference(f'permissions/{request_id}')
        is_leave_record = not perm_ref.get()
        if is_leave_record:
            perm_ref = db.reference(f'leaves/{request_id}')
        if not perm_ref.get():
            flash('Permission request not found!', 'error')
            return redirect(url_for('permissions'))
        perm_ref.update({
            'status': 'rejected' if is_leave_record else 'REJECTED',
            'approvedBy': session.get('username', 'Admin'),
            'approvedAt': int(datetime.now().timestamp() * 1000),
            'approverRemarks': remarks
        })
        
        flash('Permission rejected!', 'warning')
    except Exception as e:
        flash(f'Error rejecting permission: {str(e)}', 'error')
    
    return redirect(url_for('permissions'))

# ═══════════════════════════════════════════════════════════════════════════
# SESSION MONITORING
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/sessions')
@login_required
def sessions():
    """View all work sessions with date filter"""
    try:
        status_filter = request.args.get('status', '').strip() or 'all'
        date_filter = request.args.get('date', '').strip() or 'all'
        employee_filter = request.args.get('employee', '').strip() or 'all'
        
        all_sessions = read_firebase_path('sessions')
        all_employees = read_firebase_path('users')
        
        sessions_list = []
        for sess_id, sess_data in all_sessions.items():
            if not isinstance(sess_data, dict):
                continue

            # Filter by status
            sess_status = str(sess_data.get('status', '')).upper()
            if status_filter != 'all' and sess_status != status_filter.upper():
                continue
            
            # Determine session date (prefer stored 'date' string, fallback to timestamps)
            session_date = sess_data.get('date')
            if not session_date or str(session_date).strip() in ('N/A', 'None', 'null', ''):
                ts = sess_data.get('startTime') or sess_data.get('endTime') or sess_data.get('closedAt')
                if ts:
                    try:
                        session_date = datetime.fromtimestamp(int(ts) / 1000).strftime('%Y-%m-%d')
                    except Exception:
                        session_date = 'N/A'
                else:
                    session_date = 'N/A'

            # Filter by date
            if date_filter != 'all' and session_date != date_filter:
                continue
            
            # Filter by employee using all possible employee identifiers in Firebase
            if employee_filter != 'all':
                filter_candidates = get_employee_filter_candidates(all_employees, employee_filter)
                record_candidates = get_employee_identifier_candidates(sess_data)
                if not record_candidates.intersection(filter_candidates):
                    continue
            
            emp_id = get_employee_reference(sess_data)
            emp_data = get_employee_by_id(all_employees, emp_id) or all_employees.get(emp_id, {})
            
            # The session list only needs to know whether a photo reference exists,
            # not load the full image payload for every session card.
            start_photo_available = has_stored_photo(sess_data.get('startPhotoUri'))
            end_photo_available = has_stored_photo(sess_data.get('endPhotoUri'))
            
            # Normalize timestamp for sorting: startTime -> endTime -> closedAt -> session_date
            start_time = sess_data.get('startTime')
            start_time_for_sort = 0
            if start_time:
                try:
                    start_time_for_sort = int(start_time)
                except Exception:
                    start_time_for_sort = 0
            if not start_time_for_sort:
                for fallback_ts in (sess_data.get('endTime'), sess_data.get('closedAt')):
                    if fallback_ts:
                        try:
                            start_time_for_sort = int(fallback_ts)
                            break
                        except Exception:
                            pass
            if not start_time_for_sort and session_date and session_date != 'N/A':
                try:
                    start_time_for_sort = int(datetime.strptime(session_date, '%Y-%m-%d').timestamp() * 1000)
                except Exception:
                    start_time_for_sort = 0
            
            emp_name = emp_data.get('name') or sess_data.get('employeeName') or 'Unknown'
            sessions_list.append({
                'id': sess_id,
                'employeeId': emp_id,
                'employeeName': emp_name,
                'employeeInitials': get_employee_initials(emp_name),
                'employeeRole': emp_data.get('role', sess_data.get('employeeRole', 'N/A')),
                'workType': sess_data.get('workType'),
                'status': sess_data.get('status'),
                'startTime': sess_data.get('startTime'),
                'endTime': sess_data.get('endTime'),
                'totalWorkTimeMs': sess_data.get('totalWorkTimeMs', 0),
                'totalBreakTimeMs': sess_data.get('totalBreakTimeMs', 0),
                'startLocation': sess_data.get('startLocation'),
                'endLocation': sess_data.get('endLocation'),
                'startPhotoUri': sess_data.get('startPhotoUri'),
                'endPhotoUri': sess_data.get('endPhotoUri'),
                'startPhotoAvailable': start_photo_available,
                'endPhotoAvailable': end_photo_available,
                'date': session_date,
                'startTimeSort': start_time_for_sort
            })
        
        # Sort by normalized timestamp descending
        sessions_list.sort(key=lambda x: safe_num(x.get('startTimeSort')), reverse=True)
        
        # Prepare employee list for dropdown using users + session records
        employee_list = []
        seen_refs = set()
        for emp_id, emp_data in all_employees.items():
            if not isinstance(emp_data, dict):
                continue
            employee_ref = (
                emp_data.get('employeePhone') or
                emp_data.get('empPhone') or
                emp_data.get('phone') or
                emp_data.get('employeeId') or
                emp_id
            )
            seen_refs.add(str(employee_ref))
            if emp_data.get('employeeId'):
                seen_refs.add(str(emp_data.get('employeeId')))
            employee_list.append({
                'id': employee_ref,
                'name': emp_data.get('name') or 'Unknown'
            })
        for sess_data in all_sessions.values():
            if not isinstance(sess_data, dict):
                continue
            sess_emp_ref = sess_data.get('employeePhone') or sess_data.get('employeeId')
            if sess_emp_ref and str(sess_emp_ref) not in seen_refs:
                seen_refs.add(str(sess_emp_ref))
                employee_list.append({
                    'id': str(sess_emp_ref),
                    'name': sess_data.get('employeeName') or str(sess_emp_ref)
                })
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('sessions.html', 
                             sessions=sessions_list,
                             status_filter=status_filter,
                             date_filter=date_filter,
                             employee_filter=employee_filter,
                             employees=employee_list)
    except Exception as e:
        flash(f'Error loading sessions: {str(e)}', 'error')
        return render_template('sessions.html', sessions=[], employees=[])

@app.route('/sessions/<session_id>')
@login_required
def session_detail(session_id):
    """View detailed session information with photos and locations"""
    try:
        # Get session data
        sess_ref = db.reference(f'sessions/{session_id}')
        session = sess_ref.get()
        
        if not session:
            flash('Session not found!', 'error')
            return redirect(url_for('sessions'))
        
        # Get employee data using helper function
        emp_id = get_employee_reference(session)
        employees_ref = db.reference('users')
        all_employees = employees_ref.get() or {}
        employee = get_employee_by_id(all_employees, emp_id)
        
        start_photo = load_session_photo(session_id, 'start', session)
        end_photo = load_session_photo(session_id, 'end', session)
        
        session['id'] = session_id
        session['employeeId'] = emp_id or session.get('employeeId') or 'N/A'
        session['employeeName'] = employee.get('name', session.get('employeeName', 'Unknown'))
        session['employeePhone'] = employee.get('phone', session.get('employeePhone', 'N/A'))
        session['employeeRole'] = employee.get('role', session.get('employeeRole', 'N/A'))
        session['employeeInitials'] = get_employee_initials(session['employeeName'])
        session['employeePhoto'] = employee.get('photo') or employee.get('profilePhoto') or employee.get('avatar') or None
        session['startPhoto'] = start_photo
        session['endPhoto'] = end_photo

        # Keep the route limited to movement recorded during this work session.
        route_points = []
        start_time = normalize_visit_datetime(session.get('startTime'))['sort_key']
        end_time = normalize_visit_datetime(session.get('endTime'))['sort_key']
        current_time = datetime.now().timestamp()
        if not end_time:
            end_time = current_time

        start_location = session.get('startLocation') or {}
        if start_location.get('latitude') and start_location.get('longitude'):
            route_points.append({
                'latitude': start_location['latitude'],
                'longitude': start_location['longitude'],
                'label': 'Work started',
                'timestamp': session.get('startTime')
            })

        for location_id, location in get_session_locations(session_id).items():
            if not location.get('latitude') or not location.get('longitude'):
                continue
            point_time = normalize_visit_datetime(location.get('timestamp'))['sort_key']
            if point_time and (not start_time or point_time >= start_time) and point_time <= end_time:
                route_points.append({
                    'latitude': location['latitude'],
                    'longitude': location['longitude'],
                    'label': 'Employee movement',
                    'timestamp': location.get('timestamp'),
                    'id': location_id
                })

        end_location = session.get('endLocation') or {}
        if end_location.get('latitude') and end_location.get('longitude'):
            route_points.append({
                'latitude': end_location['latitude'],
                'longitude': end_location['longitude'],
                'label': 'Work ended',
                'timestamp': session.get('endTime')
            })

        route_points.sort(key=lambda point: normalize_visit_datetime(point.get('timestamp'))['sort_key'])
        session['routePoints'] = route_points

        # Some historical session records do not contain startTime/startLocation,
        # but the template expects these fields to exist for duration and map display.
        if not session.get('startTime') and route_points:
            session['startTime'] = next(
                (point.get('timestamp') for point in route_points if point.get('timestamp')),
                None
            )
        if not session.get('startLocation') and route_points:
            first_point = next((point for point in route_points if point.get('latitude') and point.get('longitude')), None)
            if first_point:
                session['startLocation'] = {
                    'latitude': first_point.get('latitude'),
                    'longitude': first_point.get('longitude')
                }
        
        return render_template(
            'session_detail.html',
            session=session,
            azure_maps_key=app.config['AZURE_MAPS_SUBSCRIPTION_KEY']
        )
    except Exception as e:
        flash(f'Error loading session details: {str(e)}', 'error')
        return redirect(url_for('sessions'))

# ═══════════════════════════════════════════════════════════════════════════
# TASK MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/tasks')
@login_required
def tasks():
    """View all tasks"""
    try:
        status_filter = request.args.get('status', '').strip() or 'all'
        employee_filter = request.args.get('employee', '').strip() or 'all'
        role_filter = request.args.get('role', '').strip() or 'all'  # field_staff or office_staff
        
        tasks_ref = db.reference('tasks')
        all_tasks = tasks_ref.get() or {}
        
        # Get all employees for filter dropdown
        employees_ref = db.reference('users')
        all_employees = employees_ref.get() or {}
        
        tasks_list = []
        for task_id, task_data in all_tasks.items():
            # Filter by status
            if status_filter != 'all' and task_data.get('status') != status_filter:
                continue
            
            # Get assigned employee info
            assigned_to = task_data.get('assignedTo', '')
            emp_data = get_employee_by_id(all_employees, assigned_to)
            
            # If no employee found by ID, try by phone
            if not emp_data:
                emp_data = all_employees.get(assigned_to, {})
            
            emp_role = emp_data.get('role', 'N/A')
            
            # Filter by role
            if role_filter != 'all' and emp_role != role_filter:
                continue
            
            # Filter by employee
            if employee_filter != 'all' and assigned_to != employee_filter:
                continue
            
            # Normalize timestamp for sorting
            assigned_date = task_data.get('assignedDate', 0)
            assigned_date_sort = 0
            try:
                if isinstance(assigned_date, str):
                    assigned_date_sort = int(assigned_date)
                else:
                    assigned_date_sort = assigned_date
            except:
                assigned_date_sort = 0
            
            tasks_list.append({
                'id': task_id,
                'title': task_data.get('title', 'Untitled'),
                'description': task_data.get('description', ''),
                'assignedTo': assigned_to,
                'assignedToName': emp_data.get('name', 'Unknown'),
                'assignedToRole': emp_role,
                'assignedBy': task_data.get('assignedBy', 'Admin'),
                'assignedDate': assigned_date,
                'assignedDateSort': assigned_date_sort,
                'dueDate': task_data.get('dueDate'),
                'priority': task_data.get('priority', 'Medium'),
                'status': task_data.get('status', 'Pending'),
                'contactNumber': task_data.get('contactNumber'),
                'contactName': task_data.get('contactName'),
                'location': task_data.get('location'),
                'service': task_data.get('service')
            })
        
        # Sort by assigned date
        tasks_list.sort(key=lambda x: safe_num(x.get('assignedDateSort')), reverse=True)
        
        # Prepare employee list for dropdown
        employee_list = [{'id': emp_id, 'name': emp_data.get('name') or 'Unknown', 'role': emp_data.get('role', 'N/A')} 
                        for emp_id, emp_data in all_employees.items() if isinstance(emp_data, dict)]
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('tasks.html', 
                             tasks=tasks_list,
                             status_filter=status_filter,
                             employee_filter=employee_filter,
                             role_filter=role_filter,
                             employees=employee_list)
    except Exception as e:
        flash(f'Error loading tasks: {str(e)}', 'error')
        return render_template('tasks.html', tasks=[], employees=[])

@app.route('/tasks/<task_id>')
@login_required
def task_detail(task_id):
    """View detailed task information"""
    try:
        # Get task data
        task_ref = db.reference(f'tasks/{task_id}')
        task = task_ref.get()
        
        if not task:
            flash('Task not found!', 'error')
            return redirect(url_for('tasks'))
        
        # Get assigned employee data
        assigned_to = task.get('assignedTo', '')
        employees_ref = db.reference('users')
        all_employees = employees_ref.get() or {}
        employee = get_employee_by_id(all_employees, assigned_to)
        
        # If no employee found by ID, try by phone
        if not employee:
            employee = all_employees.get(assigned_to, {})
        
        task['id'] = task_id
        task['assignedToName'] = employee.get('name', 'Unknown')
        task['assignedToPhone'] = employee.get('phone', 'N/A')
        task['assignedToRole'] = employee.get('role', 'N/A')
        task['assignedToEmployeeId'] = employee.get('employeeId', 'N/A')
        
        return render_template('task_detail.html', task=task)
    except Exception as e:
        flash(f'Error loading task details: {str(e)}', 'error')
        return redirect(url_for('tasks'))

@app.route('/tasks/new', methods=['GET', 'POST'])
@login_required
def new_task():
    """Create new task - redirect to field or office"""
    # Redirect to field task assignment by default
    return redirect(url_for('assign_field_task'))

@app.route('/tasks/assign/field', methods=['GET', 'POST'])
@login_required
def assign_field_task():
    """Assign task to field staff"""
    if request.method == 'POST':
        try:
            # Get form data
            title = request.form.get('title')
            description = request.form.get('description')
            assigned_to = request.form.get('assignedTo')
            priority = request.form.get('priority', 'Medium')
            due_date = request.form.get('dueDate')
            contact_name = request.form.get('contactName')
            contact_number = request.form.get('contactNumber')
            location = request.form.get('location')
            service = request.form.get('service')
            
            # Validate required fields
            if not title or not assigned_to:
                flash('Title and Assigned To are required!', 'error')
                return redirect(url_for('assign_field_task'))
            
            # Convert due date to timestamp if provided
            due_date_ts = None
            if due_date:
                try:
                    due_date_ts = int(datetime.strptime(due_date, '%Y-%m-%d').timestamp() * 1000)
                except:
                    pass
            
            # Create task
            tasks_ref = db.reference('tasks')
            new_task_ref = tasks_ref.push()
            
            task_data = {
                'title': title,
                'description': description,
                'assignedTo': assigned_to,
                'assignedBy': session.get('username', 'Admin'),
                'assignedDate': int(datetime.now().timestamp() * 1000),
                'priority': priority,
                'status': 'Pending',
                'taskType': 'field'  # Mark as field task
            }
            
            # Add optional fields
            if due_date_ts:
                task_data['dueDate'] = due_date_ts
            if contact_name:
                task_data['contactName'] = contact_name
            if contact_number:
                task_data['contactNumber'] = contact_number
            if location:
                task_data['location'] = location
            if service:
                task_data['service'] = service
            
            new_task_ref.set(task_data)
            
            flash('Field task assigned successfully!', 'success')
            return redirect(url_for('tasks'))
        except Exception as e:
            flash(f'Error creating task: {str(e)}', 'error')
            return redirect(url_for('assign_field_task'))
    
    # GET request - show form
    try:
        # Get all employees for dropdown
        employees_ref = db.reference('users')
        all_employees = employees_ref.get() or {}
        
        employee_list = [{'id': emp_id, 'name': emp_data.get('name') or 'Unknown', 'role': emp_data.get('role', 'N/A'), 'employeeId': emp_data.get('employeeId', emp_id)} 
                        for emp_id, emp_data in all_employees.items() if isinstance(emp_data, dict)]
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('task_assign_field.html', employees=employee_list)
    except Exception as e:
        flash(f'Error loading form: {str(e)}', 'error')
        return redirect(url_for('tasks'))

@app.route('/tasks/assign/office', methods=['GET', 'POST'])
@login_required
def assign_office_task():
    """Assign task to office staff"""
    if request.method == 'POST':
        try:
            # Get form data
            title = request.form.get('title')
            description = request.form.get('description')
            assignment_type = request.form.get('assignmentType', 'specific')
            assigned_to = request.form.get('assignedTo')
            priority = request.form.get('priority', 'Medium')
            due_date = request.form.get('dueDate')
            contact_name = request.form.get('contactName')
            contact_number = request.form.get('contactNumber')
            service = request.form.get('service')
            
            # Validate required fields
            if not title:
                flash('Title is required!', 'error')
                return redirect(url_for('assign_office_task'))
            
            if assignment_type == 'specific' and not assigned_to:
                flash('Please select an employee for specific assignment!', 'error')
                return redirect(url_for('assign_office_task'))
            
            # Convert due date to timestamp if provided
            due_date_ts = None
            if due_date:
                try:
                    due_date_ts = int(datetime.strptime(due_date, '%Y-%m-%d').timestamp() * 1000)
                except:
                    pass
            
            # Create task
            tasks_ref = db.reference('tasks')
            new_task_ref = tasks_ref.push()
            
            task_data = {
                'title': title,
                'description': description,
                'assignedBy': session.get('username', 'Admin'),
                'assignedDate': int(datetime.now().timestamp() * 1000),
                'priority': priority,
                'status': 'Pending',
                'taskType': 'office'  # Mark as office task
            }
            
            # Handle assignment type
            if assignment_type == 'all':
                # Assign to all office staff
                task_data['assignedRole'] = 'office_staff'
                task_data['assignedTo'] = 'ALL_OFFICE_STAFF'
            else:
                # Assign to specific employee
                task_data['assignedTo'] = assigned_to
            
            # Add optional fields
            if due_date_ts:
                task_data['dueDate'] = due_date_ts
            if contact_name:
                task_data['contactName'] = contact_name
            if contact_number:
                task_data['contactNumber'] = contact_number
            if service:
                task_data['service'] = service
            
            new_task_ref.set(task_data)
            
            if assignment_type == 'all':
                flash('Office task assigned to all office staff successfully!', 'success')
            else:
                flash('Office task assigned successfully!', 'success')
            return redirect(url_for('tasks'))
        except Exception as e:
            flash(f'Error creating task: {str(e)}', 'error')
            return redirect(url_for('assign_office_task'))
    
    # GET request - show form
    try:
        # Get all employees for dropdown
        employees_ref = db.reference('users')
        all_employees = employees_ref.get() or {}
        
        employee_list = [{'id': emp_id, 'name': emp_data.get('name') or 'Unknown', 'role': emp_data.get('role', 'N/A'), 'employeeId': emp_data.get('employeeId', emp_id)} 
                        for emp_id, emp_data in all_employees.items() if isinstance(emp_data, dict)]
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('task_assign_office.html', employees=employee_list)
    except Exception as e:
        flash(f'Error loading form: {str(e)}', 'error')
        return redirect(url_for('tasks'))

@app.route('/tasks/<task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
    """Edit existing task"""
    if request.method == 'POST':
        try:
            # Get form data
            title = request.form.get('title')
            description = request.form.get('description')
            assigned_to = request.form.get('assignedTo')
            priority = request.form.get('priority', 'Medium')
            status = request.form.get('status', 'Pending')
            due_date = request.form.get('dueDate')
            contact_name = request.form.get('contactName')
            contact_number = request.form.get('contactNumber')
            location = request.form.get('location')
            service = request.form.get('service')
             # Validate required fields
            if not title or not assigned_to:
                flash('Title and Assigned To are required!', 'error')
                return redirect(url_for('edit_task', task_id=task_id))
            
            # Convert due date to timestamp if provided
            due_date_ts = None
            if due_date:
                try:
                    due_date_ts = int(datetime.strptime(due_date, '%Y-%m-%d').timestamp() * 1000)
                except:
                    pass
            
            # Update task
            task_ref = db.reference(f'tasks/{task_id}')
            
            update_data = {
                'title': title,
                'description': description,
                'assignedTo': assigned_to,
                'priority': priority,
                'status': status
            }
            
            # Add optional fields
            if due_date_ts:
                update_data['dueDate'] = due_date_ts
            if contact_name:
                update_data['contactName'] = contact_name
            if contact_number:
                update_data['contactNumber'] = contact_number
            if location:
                update_data['location'] = location
            if service:
                update_data['service'] = service
            
            task_ref.update(update_data)
            
            flash('Task updated successfully!', 'success')
            return redirect(url_for('task_detail', task_id=task_id))
        except Exception as e:
            flash(f'Error updating task: {str(e)}', 'error')
            return redirect(url_for('edit_task', task_id=task_id))
    
    # GET request - show form
    try:
        # Get task data
        task_ref = db.reference(f'tasks/{task_id}')
        task = task_ref.get()
        
        if not task:
            flash('Task not found!', 'error')
            return redirect(url_for('tasks'))
        
        task['id'] = task_id
        
        # Get all employees for dropdown
        employees_ref = db.reference('users')
        all_employees = employees_ref.get() or {}
        
        employee_list = [{'id': emp_id, 'name': emp_data.get('name') or 'Unknown', 'role': emp_data.get('role', 'N/A'), 'employeeId': emp_data.get('employeeId', emp_id)} 
                        for emp_id, emp_data in all_employees.items() if isinstance(emp_data, dict)]
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('task_form.html', task=task, employees=employee_list)
    except Exception as e:
        flash(f'Error loading task: {str(e)}', 'error')
        return redirect(url_for('tasks'))

@app.route('/tasks/<task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    """Delete task"""
    try:
        task_ref = db.reference(f'tasks/{task_id}')
        task_ref.delete()
        
        flash('Task deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting task: {str(e)}', 'error')
    
    return redirect(url_for('tasks'))

@app.route('/tasks/<task_id>/status', methods=['POST'])
@login_required
def update_task_status(task_id):
    """Update task status"""
    try:
        new_status = request.form.get('status')
        
        if new_status not in ['Pending', 'In Progress', 'Completed']:
            flash('Invalid status!', 'error')
            return redirect(url_for('task_detail', task_id=task_id))
        
        task_ref = db.reference(f'tasks/{task_id}')
        task_ref.update({'status': new_status})
        
        flash(f'Task status updated to {new_status}!', 'success')
    except Exception as e:
        flash(f'Error updating status: {str(e)}', 'error')
    
    return redirect(url_for('task_detail', task_id=task_id))

# ═══════════════════════════════════════════════════════════════════════════
# REPORTS
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/visits')
@login_required
def visits():
    """View all field visits with photos"""
    try:
        date_filter = request.args.get('date', '').strip() or 'all'
        employee_filter = request.args.get('employee', '').strip() or 'all'
        
        all_visits = read_firebase_path('visits')
        all_employees = read_firebase_path('users')
        all_sessions = read_firebase_path('sessions')
        session_photo_available = {
            session_id: (has_stored_photo(session_data.get('startPhotoUri')) or
                          has_stored_photo(session_data.get('endPhotoUri')))
            for session_id, session_data in all_sessions.items()
            if isinstance(session_data, dict)
        }
        
        photo_map = {}
        visits_list = []
        linked_session_ids = {
            visit_data.get('sessionId')
            for visit_data in all_visits.values()
            if isinstance(visit_data, dict) and visit_data.get('sessionId')
        }
        for visit_id, visit_data in all_visits.items():
            if not isinstance(visit_data, dict):
                continue

            created_at = visit_data.get('date') or visit_data.get('createdAt') or visit_data.get('timestamp') or '0'
            visit_info = normalize_visit_datetime(created_at)
            visit_date = visit_data.get('date') or visit_info['date']
            if date_filter != 'all' and visit_date != date_filter:
                continue

            if employee_filter != 'all':
                filter_candidates = get_employee_filter_candidates(all_employees, employee_filter)
                record_candidates = get_employee_identifier_candidates(visit_data)
                if not record_candidates.intersection(filter_candidates):
                    continue

            emp_id = get_employee_reference(visit_data)
            emp_data = get_employee_by_id(all_employees, emp_id) or all_employees.get(emp_id, {})

            photo_data = photo_map.get(visit_id)
            has_session_photo = session_photo_available.get(visit_data.get('sessionId'), False)

            timestamp = visit_data.get('timestamp', created_at)
            timestamp_for_sort = visit_info['sort_key']

            emp_name = emp_data.get('name') or visit_data.get('employeeName') or 'Unknown'
            emp_photo = None
            has_visit_photo = bool(
                visit_data.get('photoUri') and not str(visit_data.get('photoUri')).startswith(('/data/', 'file:'))
                or visit_data.get('photo') or visit_data.get('imageBase64')
            )

            visits_list.append({
                'id': visit_id,
                'employeeId': emp_id,
                'employeeName': emp_name,
                'employeeInitials': get_employee_initials(emp_name),
                'employeePhoto': emp_photo,
                'date': visit_date,
                'time': visit_info['time'],
                'datetime': visit_info['datetime'],
                'timestamp': timestamp,
                'timestampSort': timestamp_for_sort,
                'leadName': visit_data.get('leadName', 'N/A'),
                'leadPhone': visit_data.get('leadPhone', 'N/A'),
                'latitude': visit_data.get('latitude'),
                'longitude': visit_data.get('longitude'),
                'notes': visit_data.get('notes', ''),
                'photoId': photo_data['id'] if photo_data else None,
                'hasPhoto': has_visit_photo or has_session_photo
            })

        for session_id, session_data in all_sessions.items():
            if not isinstance(session_data, dict):
                continue
            if session_id in linked_session_ids:
                continue

            end_time = session_data.get('endTime') or session_data.get('startTime') or '0'
            visit_info = normalize_visit_datetime(end_time)
            session_date = session_data.get('date') or visit_info['date']
            if date_filter != 'all' and session_date != date_filter:
                continue

            if employee_filter != 'all':
                filter_candidates = get_employee_filter_candidates(all_employees, employee_filter)
                record_candidates = get_employee_identifier_candidates(session_data)
                if not record_candidates.intersection(filter_candidates):
                    continue

            emp_id = get_employee_reference(session_data)
            emp_data = get_employee_by_id(all_employees, emp_id) or all_employees.get(emp_id, {})
            location = session_data.get('endLocation') or session_data.get('startLocation') or {}
            emp_name = emp_data.get('name') or session_data.get('employeeName') or 'Unknown'
            has_photo = session_photo_available.get(session_id, False)
            work_type = str(session_data.get('workType', '')).upper()
            visits_list.append({
                'id': f'session-{session_id}',
                'employeeId': emp_id,
                'employeeName': emp_name,
                'employeeInitials': get_employee_initials(emp_name),
                'employeePhoto': None,
                'date': session_date,
                'time': visit_info['time'],
                'datetime': visit_info['datetime'],
                'timestamp': end_time,
                'timestampSort': visit_info['sort_key'],
                'leadName': 'Field check-in' if work_type in ('FIELD', 'FARM', 'FIELD_WORK') else 'Work session',
                'leadPhone': 'N/A',
                'latitude': location.get('latitude') if isinstance(location, dict) else None,
                'longitude': location.get('longitude') if isinstance(location, dict) else None,
                'notes': f"{work_type.capitalize() if work_type else 'Work'} check-in",
                'photoId': None,
                'hasPhoto': has_photo
            })
        
        # Sort by newest date first, then earliest time first within the same date
        for visit in visits_list:
            date_value = visit.get('date')
            try:
                visit['dateSort'] = datetime.strptime(date_value, '%Y-%m-%d').timestamp()
            except Exception:
                visit['dateSort'] = 0

        visits_list.sort(key=lambda x: (-safe_num(x.get('dateSort')), safe_num(x.get('timestampSort'))))
        
        # Prepare employee list for dropdown using users + visits records
        employee_list = []
        seen_refs = set()
        for emp_id, emp_data in all_employees.items():
            if not isinstance(emp_data, dict):
                continue
            employee_ref = (
                emp_data.get('employeePhone') or
                emp_data.get('empPhone') or
                emp_data.get('phone') or
                emp_data.get('employeeId') or
                emp_id
            )
            seen_refs.add(str(employee_ref))
            if emp_data.get('employeeId'):
                seen_refs.add(str(emp_data.get('employeeId')))
            employee_list.append({
                'id': employee_ref,
                'name': emp_data.get('name') or 'Unknown'
            })
        for vis_data in all_visits.values():
            if not isinstance(vis_data, dict):
                continue
            vis_emp = vis_data.get('employeeId')
            if vis_emp and str(vis_emp) not in seen_refs:
                seen_refs.add(str(vis_emp))
                employee_list.append({
                    'id': str(vis_emp),
                    'name': f"{vis_emp} (Field Staff)"
                })
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('visits.html', 
                             visits=visits_list,
                             date_filter=date_filter,
                             employee_filter=employee_filter,
                             employees=employee_list)
    except Exception as e:
        flash(f'Error loading visits: {str(e)}', 'error')
        return render_template('visits.html', visits=[], employees=[])

@app.route('/visits/<visit_id>')
@login_required
def visit_detail(visit_id):
    """View detailed visit information with photo"""
    try:
        generated_from_session = visit_id.startswith('session-')
        session_data = None
        session_id = None
        if generated_from_session:
            session_id = visit_id.removeprefix('session-')
            session_data = db.reference(f'sessions/{session_id}').get()
            if session_data:
                location = session_data.get('endLocation') or session_data.get('startLocation') or {}
                work_type = str(session_data.get('workType', '')).upper()
                visit = {
                    'employeeId': session_data.get('employeeId'),
                    'createdAt': session_data.get('endTime') or session_data.get('startTime'),
                    'leadName': 'Field check-in' if work_type in ('FIELD', 'FARM', 'FIELD_WORK') else 'Work session',
                    'leadPhone': 'N/A',
                    'latitude': location.get('latitude') if isinstance(location, dict) else None,
                    'longitude': location.get('longitude') if isinstance(location, dict) else None,
                    'notes': f"{work_type.capitalize() if work_type else 'Work'} check-in"
                }
            else:
                visit = None
        else:
            visit = None

        # Get visit data
        if visit is None:
            visit_ref = db.reference(f'visits/{visit_id}')
            visit = visit_ref.get()
            session_id = visit.get('sessionId') if visit else None
            if session_id:
                session_data = read_firebase_path(f'sessions/{session_id}')
        
        if not visit:
            flash('Visit not found!', 'error')
            return redirect(url_for('visits'))
        
        # Get employee data using cached helper function
        emp_id = visit.get('employeeId')
        all_employees = read_firebase_path('users')
        employee = get_employee_by_id(all_employees, emp_id) or {}

        route_points = []
        if session_data:
            start_location = session_data.get('startLocation') or {}
            if start_location.get('latitude') and start_location.get('longitude'):
                route_points.append({
                    'latitude': start_location['latitude'],
                    'longitude': start_location['longitude'],
                    'label': 'Work started',
                    'timestamp': session_data.get('startTime')
                })

        if session_id and not generated_from_session:
            session_visits = read_firebase_path('visits')
            for item in session_visits.values():
                if item.get('sessionId') == session_id and item.get('latitude') and item.get('longitude'):
                    route_points.append({
                        'latitude': item['latitude'],
                        'longitude': item['longitude'],
                        'label': item.get('leadName') or 'Visit',
                        'timestamp': item.get('timestamp') or item.get('createdAt')
                    })

        if (not generated_from_session and visit.get('latitude') and
                visit.get('longitude') and not any(
                    point['timestamp'] == visit.get('timestamp')
                    for point in route_points
                )):
            route_points.append({
                'latitude': visit['latitude'],
                'longitude': visit['longitude'],
                'label': visit.get('leadName') or 'Visit',
                'timestamp': visit.get('timestamp') or visit.get('createdAt')
            })

        if session_data:
            end_location = session_data.get('endLocation') or {}
            if end_location.get('latitude') and end_location.get('longitude'):
                route_points.append({
                    'latitude': end_location['latitude'],
                    'longitude': end_location['longitude'],
                    'label': 'Work ended',
                    'timestamp': session_data.get('endTime')
                })

            route_points.sort(
                key=lambda point: normalize_visit_datetime(point.get('timestamp'))['sort_key']
            )

        visit['routePoints'] = route_points
        visit['workStartTime'] = session_data.get('startTime') if session_data else None
        visit['workEndTime'] = session_data.get('endTime') if session_data else None

        # Resolve visit photo from photoUri or photo or imageBase64
        photo_val = visit.get('photo') or visit.get('imageBase64') or visit.get('imageUrl')
        if not photo_val and visit.get('photoUri'):
            photo_uri = str(visit.get('photoUri')).strip()
            if photo_uri.startswith('visit_photos/'):
                try:
                    vp = db.reference(photo_uri).get()
                    if isinstance(vp, dict):
                        photo_val = vp.get('imageBase64') or vp.get('imageUrl') or vp.get('downloadUrl')
                except Exception:
                    pass
            elif photo_uri.startswith(('data:image', 'http://', 'https://')):
                photo_val = photo_uri

        if photo_val:
            photo_val = normalize_photo(photo_val)

        # Fallback to session photo only if no visit photo exists
        if not photo_val and session_id:
            photo_val = load_session_photo(session_id, 'start', session_data) or load_session_photo(session_id, 'end', session_data)

        visit['photo'] = photo_val
        visit['startPhoto'] = photo_val
        visit['endPhoto'] = photo_val
        visit['employeePhoto'] = None
        visit['employeeInitials'] = get_employee_initials(employee.get('name', 'Unknown'))
        
        visit['id'] = visit_id
        visit['employeeName'] = employee.get('name', 'Unknown')
        visit['employeePhone'] = employee.get('phone', 'N/A')
        visit['employeeRole'] = employee.get('role', 'N/A')
        return render_template(
            'visit_detail.html',
            visit=visit,
            azure_maps_key=app.config['AZURE_MAPS_SUBSCRIPTION_KEY']
        )
    except Exception as e:
        flash(f'Error loading visit details: {str(e)}', 'error')
        return redirect(url_for('visits'))

@app.route('/reports')
@login_required
def reports():
    """Generate various reports"""
    try:
        report_type = request.args.get('type', '').strip() or 'attendance'
        month = request.args.get('month', '').strip() or datetime.now().strftime('%Y-%m')
        employee_id = request.args.get('employee', '').strip() or 'all'
        
        # Get all employees
        employees_ref = db.reference('users')
        all_employees = employees_ref.get() or {}
        
        report_data = []
        
        if report_type == 'attendance':
            # Attendance summary report
            summary_ref = db.reference('attendanceSummary')
            all_summaries = summary_ref.get() or {}
            
            for emp_id, months in all_summaries.items():
                if employee_id != 'all' and emp_id != employee_id:
                    continue
                    
                if month in months:
                    summary = months[month]
                    # Use helper function to find employee by employeeId
                    emp_data = get_employee_by_id(all_employees, emp_id)
                    
                    report_data.append({
                        'employeeId': emp_id,
                        'employeeName': emp_data.get('name', 'Unknown'),
                        'employeeRole': emp_data.get('role', 'N/A'),
                        'totalWorkDays': summary.get('totalWorkDays', 0),
                        'onTimeDays': summary.get('onTimeDays', 0),
                        'lateStartDays': summary.get('lateStartDays', 0),
                        'halfDays': summary.get('halfDays', 0),
                        'averageLateMinutes': summary.get('averageLateMinutes', 0)
                    })
        
        elif report_type == 'sessions':
            # Work sessions report
            sessions_ref = db.reference('sessions')
            all_sessions = sessions_ref.get() or {}
            
            # Group sessions by employee
            employee_sessions = {}
            for sess_id, sess_data in all_sessions.items():
                start_time = sess_data.get('startTime', 0)
                if start_time:
                    try:
                        # Handle both string and int timestamps
                        if isinstance(start_time, str):
                            start_time = int(start_time)
                        session_month = datetime.fromtimestamp(start_time / 1000).strftime('%Y-%m')
                        if session_month != month:
                            continue
                    except:
                        continue
                
                emp_id = sess_data.get('employeeId')
                if employee_id != 'all' and emp_id != employee_id:
                    continue
                
                if emp_id not in employee_sessions:
                    employee_sessions[emp_id] = {
                        'total_sessions': 0,
                        'office_sessions': 0,
                        'field_sessions': 0,
                        'farm_sessions': 0,
                        'total_work_time': 0,
                        'total_break_time': 0,
                        'completed_sessions': 0,
                        'active_sessions': 0
                    }
                
                employee_sessions[emp_id]['total_sessions'] += 1
                
                work_type = sess_data.get('workType', 'OFFICE')
                if work_type == 'OFFICE':
                    employee_sessions[emp_id]['office_sessions'] += 1
                elif work_type == 'FIELD':
                    employee_sessions[emp_id]['field_sessions'] += 1
                elif work_type == 'FARM':
                    employee_sessions[emp_id]['farm_sessions'] += 1
                
                employee_sessions[emp_id]['total_work_time'] += safe_num(sess_data.get('totalWorkTimeMs'), 0)
                employee_sessions[emp_id]['total_break_time'] += safe_num(sess_data.get('totalBreakTimeMs'), 0)
                
                if sess_data.get('status') == 'CLOSED':
                    employee_sessions[emp_id]['completed_sessions'] += 1
                else:
                    employee_sessions[emp_id]['active_sessions'] += 1
            
            # Convert to report data
            for emp_id, stats in employee_sessions.items():
                # Use helper function to find employee by employeeId
                emp_data = get_employee_by_id(all_employees, emp_id)
                report_data.append({
                    'employeeId': emp_id,
                    'employeeName': emp_data.get('name', 'Unknown'),
                    'employeeRole': emp_data.get('role', 'N/A'),
                    'totalSessions': stats['total_sessions'],
                    'officeSessions': stats['office_sessions'],
                    'fieldSessions': stats['field_sessions'],
                    'farmSessions': stats['farm_sessions'],
                    'completedSessions': stats['completed_sessions'],
                    'activeSessions': stats['active_sessions'],
                    'totalWorkHours': round(stats['total_work_time'] / (1000 * 60 * 60), 2),
                    'totalBreakHours': round(stats['total_break_time'] / (1000 * 60 * 60), 2),
                    'avgWorkHoursPerSession': round(stats['total_work_time'] / (1000 * 60 * 60) / stats['total_sessions'], 2) if stats['total_sessions'] > 0 else 0
                })
        
        elif report_type == 'permissions':
            # Permissions report
            permissions_ref = db.reference('permissions')
            all_permissions = permissions_ref.get() or {}
            
            # Group permissions by employee
            employee_permissions = {}
            for perm_id, perm_data in all_permissions.items():
                requested_at = perm_data.get('requestedAt', 0)
                if requested_at:
                    try:
                        # Handle both string and int timestamps
                        if isinstance(requested_at, str):
                            requested_at = int(requested_at)
                        perm_month = datetime.fromtimestamp(requested_at / 1000).strftime('%Y-%m')
                        if perm_month != month:
                            continue
                    except:
                        continue
                
                emp_id = perm_data.get('employeeId')
                if employee_id != 'all' and emp_id != employee_id:
                    continue
                
                if emp_id not in employee_permissions:
                    employee_permissions[emp_id] = {
                        'total': 0,
                        'approved': 0,
                        'rejected': 0,
                        'pending': 0,
                        'late_start': 0,
                        'casual_leave': 0,
                        'medical_leave': 0,
                        'emergency': 0,
                        'planned_leave': 0
                    }
                
                employee_permissions[emp_id]['total'] += 1
                
                status = perm_data.get('status', 'PENDING')
                if status == 'APPROVED':
                    employee_permissions[emp_id]['approved'] += 1
                elif status == 'REJECTED':
                    employee_permissions[emp_id]['rejected'] += 1
                else:
                    employee_permissions[emp_id]['pending'] += 1
                
                perm_type = perm_data.get('type', '')
                if perm_type == 'LATE_START':
                    employee_permissions[emp_id]['late_start'] += 1
                elif perm_type == 'CASUAL_LEAVE':
                    employee_permissions[emp_id]['casual_leave'] += 1
                elif perm_type == 'MEDICAL_LEAVE':
                    employee_permissions[emp_id]['medical_leave'] += 1
                elif perm_type == 'EMERGENCY':
                    employee_permissions[emp_id]['emergency'] += 1
                elif perm_type == 'PLANNED_LEAVE':
                    employee_permissions[emp_id]['planned_leave'] += 1
            
            # Convert to report data
            for emp_id, stats in employee_permissions.items():
                # Use helper function to find employee by employeeId
                emp_data = get_employee_by_id(all_employees, emp_id)
                report_data.append({
                    'employeeId': emp_id,
                    'employeeName': emp_data.get('name', 'Unknown'),
                    'employeeRole': emp_data.get('role', 'N/A'),
                    'totalRequests': stats['total'],
                    'approved': stats['approved'],
                    'rejected': stats['rejected'],
                    'pending': stats['pending'],
                    'lateStart': stats['late_start'],
                    'casualLeave': stats['casual_leave'],
                    'medicalLeave': stats['medical_leave'],
                    'emergency': stats['emergency'],
                    'plannedLeave': stats['planned_leave']
                })
        
        # Prepare employee list for dropdown (use employeeId as id)
        employee_list = []
        for phone, emp_data in all_employees.items():
            if not isinstance(emp_data, dict):
                continue
            employee_list.append({
                'id': emp_data.get('employeeId', phone),
                'name': emp_data.get('name') or 'Unknown'
            })
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('reports.html', 
                             report_type=report_type,
                             report_data=report_data,
                             month=month,
                             employee_id=employee_id,
                             employees=employee_list)
    except Exception as e:
        flash(f'Error generating report: {str(e)}', 'error')
        return render_template('reports.html', report_type='attendance', report_data=[], employees=[])

@app.route('/reports/print')
@login_required
def report_print():
    """Interactive print and export page covering all operational modules."""
    try:
        report_type = request.args.get('type', '').strip().lower() or 'all'
        month = request.args.get('month', '').strip()
        employee_id = request.args.get('employee', '').strip() or 'all'

        # Get all employees
        employees_ref = db.reference('users')
        all_employees = employees_ref.get() or {}

        # Prepare employee list for dropdown
        employee_list = []
        for phone, emp_data in all_employees.items():
            if not isinstance(emp_data, dict):
                continue
            e_id = emp_data.get('employeeId', phone)
            employee_list.append({
                'id': e_id,
                'name': emp_data.get('name') or 'Unknown'
            })
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())

        selected_employee_name = 'All Employees'
        if employee_id != 'all':
            emp_info = get_employee_by_id(all_employees, employee_id)
            selected_employee_name = emp_info.get('name') or employee_id

        # 1. Attendance Summary
        attendance_data = []
        summary_ref = db.reference('attendanceSummary')
        all_summaries = summary_ref.get() or {}
        for emp_key, months in all_summaries.items():
            if employee_id != 'all' and emp_key != employee_id:
                continue
            if isinstance(months, dict):
                for m_key, summary in months.items():
                    if month and month != 'all' and m_key != month:
                        continue
                    emp_data = get_employee_by_id(all_employees, emp_key)
                    attendance_data.append({
                        'employeeId': emp_key,
                        'employeeName': emp_data.get('name', 'Unknown'),
                        'employeeRole': emp_data.get('role', 'N/A'),
                        'month': m_key,
                        'totalWorkDays': safe_num(summary.get('totalWorkDays', 0)),
                        'onTimeDays': safe_num(summary.get('onTimeDays', 0)),
                        'lateStartDays': safe_num(summary.get('lateStartDays', 0)),
                        'halfDays': safe_num(summary.get('halfDays', 0)),
                        'averageLateMinutes': safe_num(summary.get('averageLateMinutes', 0))
                    })
        attendance_data.sort(key=lambda x: (str(x.get('month', '')), str(x.get('employeeName', '')).lower()), reverse=True)

        # 2. Permissions & Leaves
        permissions_data = []
        all_perms = db.reference('permissions').get() or {}
        for perm_id, perm in all_perms.items():
            if not isinstance(perm, dict):
                continue
            emp_ref = perm.get('employeeId') or perm.get('phone') or perm.get('empPhone')
            if employee_id != 'all' and emp_ref != employee_id:
                continue
            req_time = perm.get('requestedAt') or perm.get('timestamp') or perm.get('createdAt')
            dt_norm = normalize_visit_datetime(req_time)
            p_month = dt_norm.get('date', '')[:7] if dt_norm.get('date') != 'N/A' else ''
            if not p_month and perm.get('date'):
                p_month = str(perm.get('date'))[:7]
            if month and month != 'all' and p_month and p_month != month:
                continue
            emp_data = get_employee_by_id(all_employees, emp_ref)
            permissions_data.append({
                'id': perm_id,
                'employeeId': emp_ref or 'N/A',
                'employeeName': emp_data.get('name', perm.get('employeeName', 'Unknown')),
                'type': perm.get('type', perm.get('permissionType', 'N/A')),
                'reason': perm.get('reason', 'N/A'),
                'status': perm.get('status', 'PENDING'),
                'date': dt_norm.get('date') if dt_norm.get('date') != 'N/A' else perm.get('date', 'N/A'),
                'time': dt_norm.get('time') if dt_norm.get('time') != 'N/A' else perm.get('time', 'N/A'),
                'startDate': perm.get('startDate', 'N/A'),
                'endDate': perm.get('endDate', 'N/A'),
                'approvedBy': perm.get('approvedBy', 'N/A')
            })
        permissions_data.sort(key=lambda x: str(x.get('date', '')), reverse=True)

        # 3. Visits
        visits_data = []
        all_visits = read_firebase_path('visits') or db.reference('visits').get() or {}
        for visit_id, visit in all_visits.items():
            if not isinstance(visit, dict):
                continue
            emp_ref = get_employee_reference(visit) or visit.get('employeeId')
            if employee_id != 'all' and emp_ref != employee_id:
                continue
            v_time = visit.get('createdAt') or visit.get('timestamp')
            v_dt = normalize_visit_datetime(v_time)
            v_month = v_dt.get('date', '')[:7] if v_dt.get('date') != 'N/A' else ''
            if not v_month and visit.get('date'):
                v_month = str(visit.get('date'))[:7]
            if month and month != 'all' and v_month and v_month != month:
                continue
            emp_data = get_employee_by_id(all_employees, emp_ref)
            visits_data.append({
                'id': visit_id,
                'employeeId': emp_ref or 'N/A',
                'employeeName': emp_data.get('name', visit.get('employeeName', 'Unknown')),
                'leadName': visit.get('leadName', 'N/A'),
                'leadPhone': visit.get('leadPhone', 'N/A'),
                'date': v_dt.get('date') if v_dt.get('date') != 'N/A' else visit.get('date', 'N/A'),
                'time': v_dt.get('time') if v_dt.get('time') != 'N/A' else visit.get('time', 'N/A'),
                'status': visit.get('status', 'COMPLETED'),
                'notes': visit.get('notes', 'N/A')
            })
        visits_data.sort(key=lambda x: str(x.get('date', '')), reverse=True)

        # 4. Sessions
        sessions_data = []
        all_sess = db.reference('sessions').get() or {}
        for sess_id, sess in all_sess.items():
            if not isinstance(sess, dict):
                continue
            emp_ref = get_employee_reference(sess) or sess.get('employeeId')
            if employee_id != 'all' and emp_ref != employee_id:
                continue
            s_time = sess.get('startTime') or sess.get('createdAt')
            s_dt = normalize_visit_datetime(s_time)
            s_month = s_dt.get('date', '')[:7] if s_dt.get('date') != 'N/A' else ''
            if not s_month and sess.get('date'):
                s_month = str(sess.get('date'))[:7]
            if month and month != 'all' and s_month and s_month != month:
                continue
            emp_data = get_employee_by_id(all_employees, emp_ref)
            end_dt = normalize_visit_datetime(sess.get('endTime'))
            sessions_data.append({
                'id': sess_id,
                'employeeId': emp_ref or 'N/A',
                'employeeName': emp_data.get('name', sess.get('employeeName', 'Unknown')),
                'workType': sess.get('workType', 'FIELD'),
                'status': sess.get('status', 'CLOSED'),
                'date': s_dt.get('date') if s_dt.get('date') != 'N/A' else sess.get('date', 'N/A'),
                'startTime': s_dt.get('time') if s_dt.get('time') != 'N/A' else 'N/A',
                'endTime': end_dt.get('time') if sess.get('endTime') else 'Active',
                'workDurationMs': safe_num(sess.get('totalWorkTimeMs'), 0),
                'breakDurationMs': safe_num(sess.get('totalBreakTimeMs'), 0)
            })
        sessions_data.sort(key=lambda x: str(x.get('date', '')), reverse=True)

        # 5. Tasks
        tasks_data = []
        all_tasks = db.reference('tasks').get() or {}
        for task_id, task in all_tasks.items():
            if not isinstance(task, dict):
                continue
            emp_ref = task.get('assignedTo', '')
            if employee_id != 'all' and emp_ref != employee_id:
                continue
            t_time = task.get('createdAt') or task.get('timestamp')
            t_dt = normalize_visit_datetime(t_time)
            t_month = t_dt.get('date', '')[:7] if t_dt.get('date') != 'N/A' else ''
            if not t_month and task.get('dueDate'):
                t_month = str(task.get('dueDate'))[:7]
            if month and month != 'all' and t_month and t_month != month:
                continue
            emp_data = get_employee_by_id(all_employees, emp_ref)
            tasks_data.append({
                'id': task_id,
                'title': task.get('title', 'Untitled Task'),
                'employeeId': emp_ref or 'N/A',
                'employeeName': emp_data.get('name', task.get('employeeName', 'Unknown')),
                'type': task.get('type', 'field'),
                'status': task.get('status', 'PENDING'),
                'priority': task.get('priority', 'MEDIUM'),
                'dueDate': task.get('dueDate', 'N/A'),
                'createdAt': t_dt.get('date', 'N/A')
            })
        tasks_data.sort(key=lambda x: str(x.get('createdAt', '')), reverse=True)

        # 6. Travel Expenses
        expenses_data = []
        all_exp = db.reference('travelExpenses').get() or {}
        for exp_id, exp in all_exp.items():
            if not isinstance(exp, dict):
                continue
            emp_ref = exp.get('employeeId', '')
            if employee_id != 'all' and emp_ref != employee_id:
                continue
            exp_date = exp.get('date', '')
            exp_month = exp_date[:7] if exp_date else ''
            if not exp_month and exp.get('createdAt'):
                exp_month = str(exp.get('createdAt'))[:7]
            if month and month != 'all' and exp_month and exp_month != month:
                continue
            emp_data = get_employee_by_id(all_employees, emp_ref)
            expenses_data.append({
                'id': exp_id,
                'employeeId': emp_ref or 'N/A',
                'employeeName': emp_data.get('name', 'Unknown'),
                'date': exp.get('date', 'N/A'),
                'time': exp.get('time', 'N/A'),
                'distance': safe_num(exp.get('distanceKm', exp.get('distance', 0))),
                'rate': safe_num(exp.get('ratePerKm', exp.get('rate', 0))),
                'amount': safe_num(exp.get('totalAmount', exp.get('amount', 0))),
                'duration': safe_num(exp.get('durationMinutes', exp.get('duration', 0))),
                'routePoints': safe_num(exp.get('routePoints', 0)),
                'sessionId': exp.get('sessionId', 'N/A')
            })
        expenses_data.sort(key=lambda x: str(x.get('date', '')), reverse=True)

        # 7. Field Measurements
        measurements_data = []
        all_meas = db.reference('fieldMeasurements').get() or db.reference('measurements').get() or {}
        for meas_id, meas in all_meas.items():
            if not isinstance(meas, dict):
                continue
            emp_ref = meas.get('employeeId', '')
            if employee_id != 'all' and emp_ref != employee_id:
                continue
            m_date = meas.get('date', '')
            m_month = m_date[:7] if m_date else ''
            if not m_month and meas.get('createdAt'):
                m_month = str(meas.get('createdAt'))[:7]
            if month and month != 'all' and m_month and m_month != month:
                continue
            emp_data = get_employee_by_id(all_employees, emp_ref)
            measurements_data.append({
                'id': meas_id,
                'employeeId': emp_ref or 'N/A',
                'employeeName': emp_data.get('name', 'Unknown'),
                'date': meas.get('date', 'N/A'),
                'time': meas.get('time', 'N/A'),
                'areaAcres': safe_num(meas.get('areaAcres', 0)),
                'areaSqMeters': safe_num(meas.get('area', 0)),
                'points': safe_num(meas.get('points', 0)),
                'distance': safe_num(meas.get('distance', 0)),
                'sessionId': meas.get('sessionId', 'N/A')
            })
        measurements_data.sort(key=lambda x: str(x.get('date', '')), reverse=True)

        return render_template(
            'report_print.html',
            report_type=report_type,
            month=month or 'all',
            employee_id=employee_id,
            selected_employee_name=selected_employee_name,
            employees=employee_list,
            attendance_data=attendance_data,
            permissions_data=permissions_data,
            visits_data=visits_data,
            sessions_data=sessions_data,
            tasks_data=tasks_data,
            expenses_data=expenses_data,
            measurements_data=measurements_data,
            generated_at=datetime.now().strftime('%Y-%m-%d %I:%M %p')
        )
    except Exception as e:
        flash(f'Error loading print report: {str(e)}', 'error')
        return redirect(url_for('reports'))

# ═══════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/api/stats')
@login_required
def api_stats():
    """Get dashboard statistics"""
    try:
        # Get employees from 'users' node
        employees_ref = db.reference('users')
        employees = employees_ref.get() or {}
        
        sessions_ref = db.reference('sessions')
        sessions = sessions_ref.get() or {}
        active_sessions = [s for s in sessions.values() if s.get('status') == 'ACTIVE']
        
        today = datetime.now().strftime('%Y-%m-%d')
        attendance_ref = db.reference('attendanceByDate').child(today)
        today_attendance = attendance_ref.get() or {}
        
        permissions_ref = db.reference('permissions')
        permissions = permissions_ref.get() or {}
        pending_permissions = [p for p in permissions.values() if p.get('status') == 'PENDING']
        
        return jsonify({
            'total_employees': len(employees),
            'active_sessions': len(active_sessions),
            'today_attendance': len(today_attendance),
            'pending_permissions': len(pending_permissions)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# TRAVEL EXPENSES
# ---------------------------------------------------------------------------

@app.route('/travel-expenses')
@login_required
def travel_expenses():
    """View all travel expenses"""
    try:
        users_ref = db.reference('users')
        all_employees = users_ref.get() or {}
        
        filter_employee = request.args.get('employee', '')
        
        expenses_data = db.reference('travelExpenses').get() or {}
        if not isinstance(expenses_data, dict):
            expenses_data = {}
        
        expenses = []
        for expense_id, expense in expenses_data.items():
            if not isinstance(expense, dict):
                continue
            employee_id = expense.get('employeeId', '')
            
            if filter_employee and employee_id != filter_employee:
                continue
            
            employee = get_employee_by_id(all_employees, employee_id)
            
            expenses.append({
                'expenseId': expense_id,
                'employeeId': employee_id,
                'employeeName': employee.get('name', 'Unknown') if employee else 'Unknown',
                'sessionId': expense.get('sessionId', ''),
                'distance': safe_num(expense.get('distance', 0)),
                'rate': safe_num(expense.get('rate', 0)),
                'amount': safe_num(expense.get('amount', 0)),
                'duration': safe_num(expense.get('duration', 0)),
                'routePoints': safe_num(expense.get('routePoints', 0)),
                'date': expense.get('date', ''),
                'time': expense.get('time', ''),
                'startLat': safe_num(expense.get('startLatitude', expense.get('startLat', 0))),
                'startLon': safe_num(expense.get('startLongitude', expense.get('startLon', 0))),
                'endLat': safe_num(expense.get('endLatitude', expense.get('endLat', 0))),
                'endLon': safe_num(expense.get('endLongitude', expense.get('endLon', 0))),
                'coordinates': expense.get('coordinates', '')
            })
        
        expenses.sort(key=lambda x: str(x.get('date') or '') + str(x.get('time') or ''), reverse=True)
        
        employee_list = []
        for phone, emp_data in all_employees.items():
            if not isinstance(emp_data, dict):
                continue
            emp_id = emp_data.get('employeeId', '')
            emp_name = emp_data.get('name') or 'Unknown'
            if emp_id:
                employee_list.append({'id': emp_id, 'name': emp_name})
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('travel_expenses.html', 
                             expenses=expenses, 
                             employees=employee_list,
                             selected_employee=filter_employee)
    except Exception as e:
        flash(f'Error loading travel expenses: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/travel-expenses/<expense_id>')
@login_required
def travel_expense_detail(expense_id):
    """View travel expense details"""
    try:
        users_ref = db.reference('users')
        all_employees = users_ref.get() or {}
        
        expense_ref = db.reference(f'travelExpenses/{expense_id}')
        expense = expense_ref.get()
        
        if not expense or not isinstance(expense, dict):
            flash('Travel expense not found', 'warning')
            return redirect(url_for('travel_expenses'))
        
        employee_id = expense.get('employeeId', '')
        employee = get_employee_by_id(all_employees, employee_id)
        
        expense_data = {
            'expenseId': expense_id,
            'employeeId': employee_id,
            'employeeName': employee.get('name', 'Unknown') if employee else 'Unknown',
            'sessionId': expense.get('sessionId', ''),
            'distance': safe_num(expense.get('distance', 0)),
            'rate': safe_num(expense.get('rate', 0)),
            'amount': safe_num(expense.get('amount', 0)),
            'duration': safe_num(expense.get('duration', 0)),
            'routePoints': safe_num(expense.get('routePoints', 0)),
            'date': expense.get('date', ''),
            'time': expense.get('time', ''),
            'startLat': safe_num(expense.get('startLatitude', expense.get('startLat', 0))),
            'startLon': safe_num(expense.get('startLongitude', expense.get('startLon', 0))),
            'endLat': safe_num(expense.get('endLatitude', expense.get('endLat', 0))),
            'endLon': safe_num(expense.get('endLongitude', expense.get('endLon', 0))),
            'coordinates': expense.get('coordinates', '')
        }
        
        return render_template('travel_expense_detail.html', expense=expense_data)
    except Exception as e:
        flash(f'Error loading travel expense: {str(e)}', 'danger')
        return redirect(url_for('travel_expenses'))


# ---------------------------------------------------------------------------
# FIELD MEASUREMENTS
# ---------------------------------------------------------------------------

@app.route('/field-measurements')
@login_required
def field_measurements():
    """View all field measurements"""
    try:
        users_ref = db.reference('users')
        all_employees = users_ref.get() or {}
        
        filter_employee = request.args.get('employee', '')
        
        measurements_data = db.reference('fieldMeasurements').get() or db.reference('measurements').get() or {}
        if not isinstance(measurements_data, dict):
            measurements_data = {}
        
        measurements = []
        for measurement_id, measurement in measurements_data.items():
            if not isinstance(measurement, dict):
                continue
            employee_id = measurement.get('employeeId', '')
            
            if filter_employee and employee_id != filter_employee:
                continue
            
            employee = get_employee_by_id(all_employees, employee_id)
            
            measurements.append({
                'measurementId': measurement_id,
                'employeeId': employee_id,
                'employeeName': employee.get('name', 'Unknown') if employee else 'Unknown',
                'area': safe_num(measurement.get('areaAcres', measurement.get('area', 0)), 0),
                'unit': measurement.get('unit', 'acres'),
                'pointsCount': safe_num(measurement.get('points', measurement.get('pointsCount', 0)), 0),
                'date': measurement.get('date', ''),
                'time': measurement.get('time', ''),
                'timestamp': measurement.get('timestamp', ''),
                'coordinates': measurement.get('coordinates', '')
            })
        
        measurements.sort(key=lambda x: safe_num(x.get('timestamp')), reverse=True)
        
        employee_list = []
        for phone, emp_data in all_employees.items():
            if not isinstance(emp_data, dict):
                continue
            emp_id = emp_data.get('employeeId', '')
            emp_name = emp_data.get('name') or 'Unknown'
            if emp_id:
                employee_list.append({'id': emp_id, 'name': emp_name})
        employee_list.sort(key=lambda x: str(x.get('name') or '').lower())
        
        return render_template('field_measurements.html', 
                             measurements=measurements, 
                             employees=employee_list,
                             selected_employee=filter_employee)
    except Exception as e:
        flash(f'Error loading field measurements: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/field-measurements/<measurement_id>')
@login_required
def field_measurement_detail(measurement_id):
    """View field measurement details"""
    try:
        users_ref = db.reference('users')
        all_employees = users_ref.get() or {}
        
        measurement = db.reference(f'fieldMeasurements/{measurement_id}').get()
        if not measurement:
            measurement = db.reference(f'measurements/{measurement_id}').get()
        
        if not measurement or not isinstance(measurement, dict):
            flash('Field measurement not found', 'warning')
            return redirect(url_for('field_measurements'))
        
        employee_id = measurement.get('employeeId', '')
        employee = get_employee_by_id(all_employees, employee_id)
        
        area_val = safe_num(measurement.get('areaAcres', measurement.get('area', 0)), 0)
        points_val = safe_num(measurement.get('points', measurement.get('pointsCount', 0)), 0)
        
        measurement_data = {
            'measurementId': measurement_id,
            'employeeId': employee_id,
            'employeeName': employee.get('name', 'Unknown') if employee else 'Unknown',
            'area': area_val,
            'unit': measurement.get('unit', 'acres'),
            'pointsCount': points_val,
            'date': measurement.get('date', ''),
            'time': measurement.get('time', ''),
            'timestamp': measurement.get('timestamp', ''),
            'coordinates': measurement.get('coordinates', '')
        }
        
        return render_template('field_measurement_detail.html', measurement=measurement_data)
    except Exception as e:
        flash(f'Error loading field measurement: {str(e)}', 'danger')
        return redirect(url_for('field_measurements'))

# ---------------------------------------------------------------------------
# ERROR HANDLERS
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

# ---------------------------------------------------------------------------
# PESTS & DISEASE ALERTS
# ---------------------------------------------------------------------------

@app.route('/pest-alerts')
@login_required
def pest_alerts():
    """View Pests & Disease Alert page"""
    try:
        alerts_ref = db.reference('pestAlerts')
        alerts_data = alerts_ref.get() or {}

        alerts = []
        for alert_id, alert in alerts_data.items():
            if not isinstance(alert, dict):
                continue
            alerts.append({
                'id': alert_id,
                'name': alert.get('name', ''),
                'crop': alert.get('crop', ''),
                'crop_emoji': alert.get('cropEmoji', '🌱'),
                'category': alert.get('category', ''),
                'severity': alert.get('severity', 'Medium'),
                'badge_type': alert.get('badgeType', 'alert'),
                'image_url': alert.get('imageUrl', ''),
                'updated_at': alert.get('updatedAt', 'Today'),
            })

        alerts.sort(key=lambda x: (str(x.get('crop') or '').lower(), str(x.get('name') or '').lower()))

        guides_ref = db.reference('pestGuides')
        guides_data = guides_ref.get() or {}
        guides = []
        for guide_id, guide in guides_data.items():
            if not isinstance(guide, dict):
                continue
            guides.append({
                'id': guide_id,
                'name': guide.get('name', ''),
                'crop': guide.get('crop', ''),
                'image_url': guide.get('imageUrl', ''),
                'url': guide.get('url', '#'),
            })
        guides.sort(key=lambda x: str(x.get('name') or '').lower())

        return render_template('pest_alerts.html', alerts=alerts, guides=guides)
    except Exception as e:
        flash(f'Error loading pest alerts: {str(e)}', 'error')
        return render_template('pest_alerts.html', alerts=[], guides=[])

@app.route('/pest-alerts/add', methods=['POST'])
@login_required
def add_pest_alert():
    """Add a new pest/disease alert"""
    try:
        name = request.form.get('name', '').strip()
        crop = request.form.get('crop', '').strip()
        crop_emoji = request.form.get('crop_emoji', '🌱').strip()
        category = request.form.get('category', '').strip()
        severity = request.form.get('severity', 'Medium').strip()
        badge_type = request.form.get('badge_type', 'alert').strip()
        image_url = request.form.get('image_url', '').strip()

        if not name or not crop or not category:
            flash('Name, Crop, and Category are required!', 'danger')
            return redirect(url_for('pest_alerts'))

        alerts_ref = db.reference('pestAlerts')
        alerts_ref.push({
            'name': name,
            'crop': crop,
            'cropEmoji': crop_emoji,
            'category': category,
            'severity': severity,
            'badgeType': badge_type,
            'imageUrl': image_url,
            'updatedAt': datetime.now().strftime('%Y-%m-%d'),
            'createdBy': session.get('username', 'admin'),
        })

        flash(f'Alert for "{name}" added successfully!', 'success')
    except Exception as e:
        flash(f'Error adding alert: {str(e)}', 'error')

    return redirect(url_for('pest_alerts'))

@app.route('/pest-alerts/<alert_id>/delete', methods=['POST'])
@login_required
def delete_pest_alert(alert_id):
    """Delete a pest/disease alert"""
    try:
        db.reference(f'pestAlerts/{alert_id}').delete()
        flash('Alert deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting alert: {str(e)}', 'error')

    return redirect(url_for('pest_alerts'))

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'True') == 'True'
    
    print(f"""
    +-----------------------------------------------------------+
    |   S2C Admin Dashboard - Employee Monitoring System        |
    +-----------------------------------------------------------+
    |   Server: http://{host}:{port}                           |
    |   Login:  {ADMIN_USERNAME}                                |
    +-----------------------------------------------------------+
    """)
    
    app.run(host=host, port=port, debug=debug)
