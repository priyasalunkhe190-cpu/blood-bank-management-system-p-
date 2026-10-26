import os
import re
import sqlite3
import hashlib
import hmac

from contextlib import closing
from datetime import datetime

from flask import Flask, flash, redirect, render_template, request, session, url_for

from ml_model import get_trained_model


APP_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(APP_DIR, "database.db")


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")  # demo fallback



def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_db()) as conn:
        cur = conn.cursor()

        # ---- donors schema + migration (SQLite) ----
        desired_donors_cols = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "name": "TEXT NOT NULL",
            "age": "INTEGER NOT NULL",
            "gender": "TEXT NOT NULL",
            "email": "TEXT UNIQUE NOT NULL",
            "phone": "TEXT",
            "blood_group": "TEXT NOT NULL",
            "address": "TEXT NOT NULL",
            "city": "TEXT NOT NULL",
            "last_donation_date": "TEXT",
            "password_hash": "TEXT NOT NULL",
        }

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='donors'")
        has_donors = cur.fetchone() is not None

        def _current_cols():
            cur.execute("PRAGMA table_info(donors)")
            rows = cur.fetchall()
            # can be tuple or mapping depending on row_factory
            try:
                return {r["name"] for r in rows}
            except Exception:
                return {r[1] for r in rows}

        def _create_donors_table(table_name: str):
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    gender TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    phone TEXT,
                    blood_group TEXT NOT NULL,
                    address TEXT NOT NULL,
                    city TEXT NOT NULL,
                    last_donation_date TEXT,
                    password_hash TEXT NOT NULL
                )
                """
            )

        if not has_donors:
            _create_donors_table("donors")
        else:
            cols = _current_cols()
            # If already migrated, just ensure table exists.
            if "password_hash" in cols and "age" in cols and "city" in cols and "address" in cols:
                # ensure table is correct enough; we still rely on inserts for new schema.
                pass
            else:
                # Migrate by recreating table.
                _create_donors_table("donors_new")

                # Read old columns if present.
                # Old schema: id, name, email, phone, blood_group, password
                # We convert old password -> password_hash (re-hash). Missing new fields get defaults.
                cur.execute(
                    """
                    SELECT id, name, email, phone, blood_group, password
                    FROM donors
                    """
                )
                old_rows = cur.fetchall()

                for r in old_rows:
                    # tuple indices based on select order above
                    donor_id, name, email, phone, blood_group, old_password = r

                    # defaults for required fields not in old schema
                    age = 18
                    gender = "Other"
                    address = ""
                    city = ""
                    last_donation_date = None

                    # hash old password deterministically if present
                    # (if old password is NULL, set a placeholder hash to avoid crashing)
                    old_password = old_password if old_password is not None else ""
                    password_hash = _hash_password(old_password)

                    cur.execute(
                        """
                        INSERT INTO donors_new (id, name, age, gender, email, phone, blood_group, address, city, last_donation_date, password_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (donor_id, name, age, gender, email, phone, blood_group, address, city, last_donation_date, password_hash),
                    )

                # replace table
                cur.execute("DROP TABLE donors")
                cur.execute("ALTER TABLE donors_new RENAME TO donors")

        # ---- admin ----

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
            """
        )

        # blood_stock
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS blood_stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                blood_group TEXT UNIQUE NOT NULL,
                units INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # blood_requests
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS blood_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donor_id INTEGER,
                hospital_id INTEGER,
                blood_group TEXT NOT NULL,
                units_requested INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY(donor_id) REFERENCES donors(id),
                FOREIGN KEY(hospital_id) REFERENCES hospitals(id)
            )
            """
        )

        # hospitals
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS hospitals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT
            )
            """
        )

        # seed default admin + stock if empty
        cur.execute("SELECT COUNT(*) as cnt FROM admin")
        if cur.fetchone()["cnt"] == 0:
            cur.execute(
                "INSERT INTO admin (username, password) VALUES (?, ?)",
                ("admin", "admin123"),
            )

        # Seed stock for common blood groups
        blood_groups = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"]
        for bg in blood_groups:
            cur.execute("SELECT id FROM blood_stock WHERE blood_group=?", (bg,))
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO blood_stock (blood_group, units) VALUES (?, ?)",
                    (bg, 10),
                )

        conn.commit()


@app.before_request
def _ensure_db():
    init_db()


def login_required(role: str):
    def decorator(view_func):
        def wrapped(*args, **kwargs):
            if session.get("role") != role:
                return redirect(url_for("index"))
            return view_func(*args, **kwargs)

        wrapped.__name__ = view_func.__name__
        return wrapped

    return decorator


@app.route("/")
def index():
    return render_template("index.html")


def _hash_password(password: str, salt: bytes | None = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    # PBKDF2-HMAC-SHA256 (fast, standard library)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    # store as hex(salt)$hex(hash)
    return f"{salt.hex()}${dk.hex()}"


def _verify_password(stored: str, provided: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.pbkdf2_hmac("sha256", provided.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def _validate_registration_form(form) -> tuple[bool, str | None, dict | None]:
    name = form.get("name", "").strip()
    age_raw = form.get("age", "").strip()
    gender = form.get("gender", "").strip()
    blood_group = form.get("blood_group", "").strip().upper()
    phone = form.get("mobile", "").strip()
    email = form.get("email", "").strip().lower()
    address = form.get("address", "").strip()
    city = form.get("city", "").strip()
    password = form.get("password", "")
    confirm = form.get("confirm_password", "")
    last_donation_date = form.get("last_donation_date", "").strip()

    # required
    if not all([name, age_raw, gender, blood_group, phone, email, address, city, password, confirm]):
        return False, "All fields are required.", None

    if not re.fullmatch(r"[A-Za-z ]+", name):
        return False, "Name should contain only alphabets and spaces.", None

    try:
        age = int(age_raw)
    except ValueError:
        return False, "Age must be a number.", None
    if age < 18 or age > 60:
        return False, "Age must be between 18 and 60.", None

    if not re.fullmatch(r"\d{10}", phone):
        return False, "Mobile number must be exactly 10 digits.", None

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return False, "Invalid email format.", None

    if password != confirm:
        return False, "Password and confirm password must match.", None

    # strong password: >=8, upper, lower, digit, special
    if len(password) < 8:
        return False, "Password must be at least 8 characters.", None
    if not re.search(r"[A-Z]", password):
        return False, "Password must include at least one uppercase letter.", None
    if not re.search(r"[a-z]", password):
        return False, "Password must include at least one lowercase letter.", None
    if not re.search(r"\d", password):
        return False, "Password must include at least one digit.", None
    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must include at least one special character.", None

    if not last_donation_date:
        return False, "Last Donation Date is required.", None

    allowed_bgs = {"A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"}
    if blood_group not in allowed_bgs:
        return False, "Invalid blood group.", None

    return True, None, {
        "name": name,
        "age": age,
        "gender": gender,
        "phone": phone,
        "blood_group": blood_group,
        "email": email,
        "address": address,
        "city": city,
        "last_donation_date": last_donation_date,
        "password": password,
    }


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        ok, err, payload = _validate_registration_form(request.form)
        if not ok:
            flash(err or "Invalid input.", "danger")
            return redirect(url_for("register"))

        assert payload is not None
        password_hash = _hash_password(payload["password"])

        with closing(get_db()) as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO donors (name, age, gender, email, phone, blood_group, address, city, last_donation_date, password_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["name"],
                        payload["age"],
                        payload["gender"],
                        payload["email"],
                        payload["phone"],
                        payload["blood_group"],
                        payload["address"],
                        payload["city"],
                        payload["last_donation_date"],
                        password_hash,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                flash("Email already registered.", "danger")
                return redirect(url_for("register"))

        flash("Registration successful. Please login.", "success")
        return redirect(url_for("donor_login"))

    return render_template("register.html")



@app.route("/donor-login", methods=["GET", "POST"])
def donor_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with closing(get_db()) as conn:
            cur = conn.cursor()
            # Check password_hash if present
            try:
                cur.execute(
                    "SELECT id, name, password_hash FROM donors WHERE email=?",
                    (email,),
                )
            except Exception:
                # fallback for extremely old schema (plain password)
                cur.execute(
                    "SELECT id, name, password FROM donors WHERE email=? AND password=?",
                    (email, password),
                )

            row = cur.fetchone()
            if row:
                if "password_hash" in row.keys():
                    if not _verify_password(row["password_hash"], password):
                        row = None
                else:
                    # old schema already matched password in query
                    pass

            if row:
                session["role"] = "donor"
                session["donor_id"] = row["id"]
                session["donor_name"] = row["name"]
                return redirect(url_for("donor_dashboard"))


        flash("Invalid login.", "danger")
        return redirect(url_for("donor_login"))

    return render_template("login.html", role_label="Donor")


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with closing(get_db()) as conn:
            cur = conn.cursor()
            # support both old plain admin password and new password_hash
            try:
                cur.execute(
                    "SELECT id, password, password_hash FROM admin WHERE username=?",
                    (username,),
                )
                row = cur.fetchone()
                if row and "password_hash" in row.keys() and row["password_hash"] is not None:
                    if not _verify_password(row["password_hash"], password):
                        row = None
                elif row and "password" in row.keys() and row["password"] is not None:
                    if row["password"] != password:
                        row = None
            except Exception:
                cur.execute(
                    "SELECT id FROM admin WHERE username=? AND password=?",
                    (username, password),
                )
                row = cur.fetchone()

            if row:
                session["role"] = "admin"
                session["admin_id"] = row["id"]
                return redirect(url_for("admin_dashboard"))


        flash("Invalid admin login.", "danger")
        return redirect(url_for("admin_login"))

    return render_template("login.html", role_label="Admin", is_admin=True)


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("index"))


@app.route("/donor-dashboard")
@login_required("donor")
def donor_dashboard():
    donor_id = session["donor_id"]

    with closing(get_db()) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, hospital_id, blood_group, units_requested, status, created_at "
            "FROM blood_requests WHERE donor_id=? ORDER BY created_at DESC",
            (donor_id,),
        )
        requests_list = cur.fetchall()

    return render_template("donor_dashboard.html", requests_list=requests_list)


@app.route("/blood-search", methods=["GET", "POST"])
def blood_search():
    results = []
    if request.method == "POST":
        blood_group = request.form.get("blood_group", "").strip().upper()
        with closing(get_db()) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, email, phone, blood_group FROM donors WHERE blood_group=?",
                (blood_group,),
            )
            results = cur.fetchall()

    return render_template("blood_search.html", results=results)


@app.route("/blood-request", methods=["GET", "POST"])
def blood_request():
    donor_id = session.get("donor_id")
    if not donor_id:
        flash("Please login as donor first.", "danger")
        return redirect(url_for("donor_login"))

    hospitals = []
    with closing(get_db()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM hospitals ORDER BY name")
        hospitals = cur.fetchall()

        # if none, seed a couple hospitals for demo
        if len(hospitals) == 0:
            cur.execute("INSERT INTO hospitals (name, email, phone) VALUES (?, ?, ?)", ("City Hospital", "contact@cityhospital.com", "111-222"))
            cur.execute("INSERT INTO hospitals (name, email, phone) VALUES (?, ?, ?)", ("Green Valley Hospital", "contact@greenvalley.com", "333-444"))
            conn.commit()
            cur.execute("SELECT id, name FROM hospitals ORDER BY name")
            hospitals = cur.fetchall()

    if request.method == "POST":
        hospital_id = request.form.get("hospital_id")
        blood_group = request.form.get("blood_group", "").strip().upper()
        units_requested = int(request.form.get("units_requested", "0"))

        if not (hospital_id and blood_group and units_requested > 0):
            flash("Please provide valid request details.", "danger")
            return redirect(url_for("blood_request"))

        with closing(get_db()) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO blood_requests (donor_id, hospital_id, blood_group, units_requested, status, created_at)
                VALUES (?, ?, ?, ?, 'Pending', ?)
                """,
                (
                    donor_id,
                    int(hospital_id),
                    blood_group,
                    units_requested,
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()

        flash("Blood request submitted to hospital. Waiting for admin approval.", "success")
        return redirect(url_for("donor_dashboard"))

    return render_template("blood_request.html", hospitals=hospitals)


@app.route("/admin-dashboard")
@login_required("admin")
def admin_dashboard():
    with closing(get_db()) as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) as cnt FROM donors")
        donors_cnt = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) as cnt FROM blood_requests")
        requests_cnt = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) as cnt FROM hospitals")
        hospitals_cnt = cur.fetchone()["cnt"]

        cur.execute(
            "SELECT blood_group, units FROM blood_stock ORDER BY blood_group"
        )
        stock = cur.fetchall()

    return render_template(
        "admin_dashboard.html",
        donors_cnt=donors_cnt,
        requests_cnt=requests_cnt,
        hospitals_cnt=hospitals_cnt,
        stock=stock,
    )


@app.route("/admin/blood-stock", methods=["GET", "POST"])
@login_required("admin")
def admin_blood_stock():
    if request.method == "POST":
        blood_group = request.form.get("blood_group", "").strip().upper()
        add_units = int(request.form.get("units", "0"))

        if not blood_group or add_units <= 0:
            flash("Enter valid stock update values.", "danger")
            return redirect(url_for("admin_blood_stock"))

        with closing(get_db()) as conn:
            cur = conn.cursor()
            # update stock
            cur.execute("SELECT units FROM blood_stock WHERE blood_group=?", (blood_group,))
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO blood_stock (blood_group, units) VALUES (?, ?)", (blood_group, add_units))
            else:
                cur.execute("UPDATE blood_stock SET units=? WHERE blood_group=?", (row["units"] + add_units, blood_group))
            conn.commit()

        flash("Stock updated.", "success")
        return redirect(url_for("admin_blood_stock"))

    with closing(get_db()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT blood_group, units FROM blood_stock ORDER BY blood_group")
        stock = cur.fetchall()

    return render_template("admin_blood_stock.html", stock=stock)


@app.route("/admin/donors")
@login_required("admin")
def admin_donors():
    with closing(get_db()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, email, phone, blood_group FROM donors ORDER BY name")
        donors = cur.fetchall()
    return render_template("admin_donors.html", donors=donors)


@app.route("/admin/hospitals")
@login_required("admin")
def admin_hospitals():
    with closing(get_db()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, email, phone FROM hospitals ORDER BY name")
        hospitals = cur.fetchall()
    return render_template("admin_hospitals.html", hospitals=hospitals)


@app.route("/admin/hospital-requests")
@login_required("admin")
def admin_hospital_requests():
    with closing(get_db()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT br.id, d.name AS donor_name, h.name AS hospital_name, br.blood_group,
                   br.units_requested, br.status, br.created_at
            FROM blood_requests br
            LEFT JOIN donors d ON d.id = br.donor_id
            LEFT JOIN hospitals h ON h.id = br.hospital_id
            ORDER BY br.created_at DESC
            """
        )
        requests_list = cur.fetchall()

    return render_template("admin_hospital_requests.html", requests_list=requests_list)


@app.route("/admin/update-request/<int:request_id>", methods=["POST"])
@login_required("admin")
def admin_update_request(request_id: int):
    new_status = request.form.get("status")
    if new_status not in ["Pending", "Approved", "Rejected", "Fulfilled"]:
        flash("Invalid status.", "danger")
        return redirect(url_for("admin_hospital_requests"))

    with closing(get_db()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT blood_group, units_requested, status FROM blood_requests WHERE id=?", (request_id,))
        row = cur.fetchone()
        if row is None:
            flash("Request not found.", "danger")
            return redirect(url_for("admin_hospital_requests"))

        # If fulfilling, decrement stock when possible
        if new_status in ["Approved", "Fulfilled"]:
            bg = row["blood_group"]
            units = int(row["units_requested"])
            cur.execute("SELECT units FROM blood_stock WHERE blood_group=?", (bg,))
            stock_row = cur.fetchone()
            if stock_row is None:
                flash("No stock record found for that blood group.", "danger")
                return redirect(url_for("admin_blood_stock"))
            if stock_row["units"] < units:
                flash("Not enough stock to fulfill the request.", "danger")
                return redirect(url_for("admin_hospital_requests"))
            cur.execute("UPDATE blood_stock SET units=? WHERE blood_group=?", (stock_row["units"] - units, bg))

        cur.execute("UPDATE blood_requests SET status=? WHERE id=?", (new_status, request_id))
        conn.commit()

    flash("Request status updated.", "success")
    return redirect(url_for("admin_hospital_requests"))


@app.route("/ml-prediction", methods=["GET", "POST"])
def ml_prediction():
    blood_group = request.form.get("blood_group", "").strip().upper() if request.method == "POST" else ""
    pred = None
    if blood_group:
        model = get_trained_model()
        try:
            pred = model.predict([[0, 0]])  # keep type stable; overwritten below
        except Exception:
            pass

        # Proper prediction using helper in ml_model
        from ml_model import predict_next_demand

        model = get_trained_model()
        pred = predict_next_demand(model, blood_group)

    return render_template("ml_prediction.html", prediction=pred, selected_blood_group=blood_group)


@app.route("/contact")
def contact():
    return render_template("contact.html")


if __name__ == "__main__":
    # Run in VS Code: python app.py
    app.run(debug=True, host="127.0.0.1", port=5000)

