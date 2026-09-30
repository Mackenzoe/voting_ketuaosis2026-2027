"""
Voting Ketua OSIS SMA IGS 2026–2027
Flask — ringan, SQLite WAL, cache CSV, admin + live videotron.
"""

from __future__ import annotations

import csv
import os
import re
import secrets
import sqlite3
import threading
from functools import lru_cache, wraps
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "votes.db"

GRADE_FILES = {
    "10": "KELAS 10.csv",
    "11": "KELAS 11.csv",
    "12": "KELAS 12.csv",
}

# Bonus khusus nomor urut 1 pada perhitungan akhir
BONUS_CANDIDATE_ID = 1
BONUS_POINTS = 15

GURU_LOGIN_USER = "Guru IGS"
GURU_LOGIN_PASS = "IGS123"

ADMIN_USER = "osissmaigs"
ADMIN_PASS = "nathangantengdanbaikhati"

CANDIDATES = [
    {
        "id": 1,
        "name": "Nanda Syahnila Mufidah",
        "image": "img/nanda.jpg",
        "theme": "pink",
    },
    {
        "id": 2,
        "name": "Marcelo William L. Tobing",
        "image": "img/marcelo.jpg",
        "theme": "blue",
    },
    {
        "id": 3,
        "name": "Treesha",
        "image": "img/treesha.jpg",
        "theme": "gold",
    },
]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "osis-igs-2026-ardraxis-voting")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 8  # 8 jam

_db_lock = threading.Lock()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _norm_nis(nis: str) -> str:
    return re.sub(r"\s+", "", (nis or "").strip())


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    with _db_lock:
        conn = get_db()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS voters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK(role IN ('siswa','guru')),
                    voter_key TEXT NOT NULL UNIQUE,
                    nama TEXT NOT NULL,
                    nis TEXT,
                    kelas TEXT,
                    tingkat TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS votes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    voter_id INTEGER NOT NULL UNIQUE,
                    candidate_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (voter_id) REFERENCES voters(id)
                );
                CREATE INDEX IF NOT EXISTS idx_voters_key ON voters(voter_key);
                CREATE INDEX IF NOT EXISTS idx_votes_candidate ON votes(candidate_id);
                """
            )
            conn.commit()
        finally:
            conn.close()


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        for raw in reader:
            cleaned = {
                (k or "").strip(): (v or "").strip()
                for k, v in raw.items()
                if k is not None
            }
            if any(cleaned.values()):
                rows.append(cleaned)
    return rows


@lru_cache(maxsize=1)
def load_students() -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for grade, filename in GRADE_FILES.items():
        rows = _read_csv(DATA_DIR / filename)
        students = []
        for r in rows:
            nis = _norm_nis(r.get("NIS", ""))
            nama = (r.get("NAMA SISWA") or "").strip()
            kelas = (r.get("KELAS") or "").strip()
            if nis and nama and kelas:
                students.append(
                    {"nis": nis, "nama": nama, "kelas": kelas, "tingkat": grade}
                )
        result[grade] = students
    return result


@lru_cache(maxsize=1)
def load_teachers() -> list[str]:
    rows = _read_csv(DATA_DIR / "DAFTAR GURU.csv")
    names: list[str] = []
    for r in rows:
        nama = ""
        for key, val in r.items():
            if key.lower().startswith("nama"):
                nama = val.strip()
                break
        if nama:
            names.append(nama)
    return names


@lru_cache(maxsize=1)
def total_eligible() -> dict[str, int]:
    students = sum(len(v) for v in load_students().values())
    teachers = len(load_teachers())
    return {
        "siswa": students,
        "guru": teachers,
        "total": students + teachers,
    }


@lru_cache(maxsize=8)
def classes_for_grade(grade: str) -> tuple[str, ...]:
    students = load_students().get(grade, [])
    seen: dict[str, None] = {}
    for s in students:
        seen.setdefault(s["kelas"], None)
    return tuple(sorted(seen.keys(), key=_class_sort_key))


def _class_sort_key(name: str):
    m = re.search(r"(\d+)", name)
    return (int(m.group(1)) if m else 999, name.casefold())


def find_student(grade: str, kelas: str, nama: str, nis: str) -> dict | None:
    target_nama = _norm(nama)
    target_nis = _norm_nis(nis)
    for s in load_students().get(grade, []):
        if (
            s["kelas"] == kelas
            and _norm_nis(s["nis"]) == target_nis
            and _norm(s["nama"]) == target_nama
        ):
            return s
    return None


def find_teacher(nama: str) -> str | None:
    target = _norm(nama)
    for t in load_teachers():
        if _norm(t) == target:
            return t
    return None


def voter_key_siswa(nis: str) -> str:
    return f"siswa:{_norm_nis(nis)}"


def voter_key_guru(nama: str) -> str:
    return f"guru:{_norm(nama)}"


def has_voted(key: str) -> bool:
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT 1 FROM voters v
            JOIN votes vt ON vt.voter_id = v.id
            WHERE v.voter_key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def voted_keys_set() -> set[str]:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT v.voter_key FROM voters v
            JOIN votes vt ON vt.voter_id = v.id
            """
        ).fetchall()
        return {r["voter_key"] for r in rows}
    finally:
        conn.close()


def raw_vote_counts() -> dict[int, int]:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT candidate_id, COUNT(*) AS total
            FROM votes
            GROUP BY candidate_id
            """
        ).fetchall()
        return {int(r["candidate_id"]): int(r["total"]) for r in rows}
    finally:
        conn.close()


def scoreboard() -> dict:
    """Hitungan suara + bonus +15 untuk nomor urut 1."""
    raw = raw_vote_counts()
    items = []
    total_raw = 0
    for c in CANDIDATES:
        votes = raw.get(c["id"], 0)
        bonus = BONUS_POINTS if c["id"] == BONUS_CANDIDATE_ID else 0
        final = votes + bonus
        total_raw += votes
        items.append(
            {
                "id": c["id"],
                "name": c["name"],
                "image": c["image"],
                "theme": c["theme"],
                "votes": votes,
                "bonus": bonus,
                "final": final,
            }
        )
    return {
        "candidates": items,
        "total_votes": total_raw,
        "bonus_candidate_id": BONUS_CANDIDATE_ID,
        "bonus_points": BONUS_POINTS,
    }


def participation_stats() -> dict:
    eligible = total_eligible()
    conn = get_db()
    try:
        voted_siswa = conn.execute(
            """
            SELECT COUNT(*) AS n FROM voters v
            JOIN votes vt ON vt.voter_id = v.id
            WHERE v.role = 'siswa'
            """
        ).fetchone()["n"]
        voted_guru = conn.execute(
            """
            SELECT COUNT(*) AS n FROM voters v
            JOIN votes vt ON vt.voter_id = v.id
            WHERE v.role = 'guru'
            """
        ).fetchone()["n"]
    finally:
        conn.close()

    voted_total = voted_siswa + voted_guru
    total = eligible["total"] or 1
    return {
        "eligible_siswa": eligible["siswa"],
        "eligible_guru": eligible["guru"],
        "eligible_total": eligible["total"],
        "voted_siswa": voted_siswa,
        "voted_guru": voted_guru,
        "voted_total": voted_total,
        "pending_total": max(eligible["total"] - voted_total, 0),
        "participation_pct": round(voted_total / total * 100, 1),
    }


def pending_voters() -> dict[str, list[dict]]:
    voted = voted_keys_set()
    pending_siswa: list[dict] = []
    for grade, students in load_students().items():
        for s in students:
            if voter_key_siswa(s["nis"]) not in voted:
                pending_siswa.append(
                    {
                        "nama": s["nama"],
                        "nis": s["nis"],
                        "kelas": s["kelas"],
                        "tingkat": grade,
                    }
                )
    pending_siswa.sort(key=lambda x: (x["tingkat"], x["kelas"], _norm(x["nama"])))

    pending_guru: list[dict] = []
    for name in load_teachers():
        if voter_key_guru(name) not in voted:
            pending_guru.append({"nama": name})

    return {"siswa": pending_siswa, "guru": pending_guru}


def register_and_vote(
    *,
    role: str,
    key: str,
    nama: str,
    candidate_id: int,
    nis: str | None = None,
    kelas: str | None = None,
    tingkat: str | None = None,
) -> tuple[bool, str]:
    if candidate_id not in {c["id"] for c in CANDIDATES}:
        return False, "Kandidat tidak valid."

    with _db_lock:
        conn = get_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM voters WHERE voter_key = ?", (key,)
            ).fetchone()
            if existing:
                voted = conn.execute(
                    "SELECT id FROM votes WHERE voter_id = ?", (existing["id"],)
                ).fetchone()
                if voted:
                    conn.rollback()
                    return False, "already"
                voter_id = existing["id"]
            else:
                cur = conn.execute(
                    """
                    INSERT INTO voters (role, voter_key, nama, nis, kelas, tingkat)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (role, key, nama, nis, kelas, tingkat),
                )
                voter_id = cur.lastrowid

            conn.execute(
                "INSERT INTO votes (voter_id, candidate_id) VALUES (?, ?)",
                (voter_id, candidate_id),
            )
            conn.commit()
            return True, "ok"
        except sqlite3.IntegrityError:
            conn.rollback()
            return False, "already"
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def clear_flow_session(*, keep_guru_auth: bool = False) -> None:
    for k in (
        "role",
        "tingkat",
        "kelas",
        "voter_nama",
        "voter_nis",
        "voter_key",
        "verified",
    ):
        session.pop(k, None)
    if not keep_guru_auth:
        session.pop("guru_auth", None)


def require_guru_auth():
    return bool(session.get("guru_auth"))


def _safe_equals(a: str, b: str) -> bool:
    """Perbandingan aman; tidak error jika panjang string beda."""
    a_b = (a or "").encode("utf-8")
    b_b = (b or "").encode("utf-8")
    if len(a_b) != len(b_b):
        return False
    return secrets.compare_digest(a_b, b_b)


def _norm_username(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def check_guru_credentials(username: str, password: str) -> bool:
    user_ok = _safe_equals(_norm_username(username), _norm_username(GURU_LOGIN_USER))
    pass_ok = _safe_equals(password or "", GURU_LOGIN_PASS)
    return user_ok and pass_ok


def check_admin_auth(username: str, password: str) -> bool:
    user_ok = _safe_equals(_norm_username(username), _norm_username(ADMIN_USER))
    pass_ok = _safe_equals(password or "", ADMIN_PASS)
    return user_ok and pass_ok


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("is_admin"):
            return view(*args, **kwargs)
        auth = request.authorization
        if auth and check_admin_auth(auth.username or "", auth.password or ""):
            session["is_admin"] = True
            return view(*args, **kwargs)
        return redirect(url_for("admin_login"))

    return wrapped



# ─── routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/peran")
def role():
    clear_flow_session()
    return render_template("role.html")


@app.route("/peran/<role_name>")
def set_role(role_name: str):
    if role_name not in ("siswa", "guru"):
        return redirect(url_for("role"))
    session["role"] = role_name
    session.pop("verified", None)
    if role_name == "siswa":
        session.pop("guru_auth", None)
        return redirect(url_for("tingkat"))
    return redirect(url_for("guru_login"))


@app.route("/siswa/tingkat")
def tingkat():
    if session.get("role") != "siswa":
        return redirect(url_for("role"))
    return render_template("tingkat.html")


@app.route("/siswa/tingkat/<grade>")
def set_tingkat(grade: str):
    if session.get("role") != "siswa":
        return redirect(url_for("role"))
    if grade not in GRADE_FILES:
        flash("Tingkat kelas tidak valid.", "error")
        return redirect(url_for("tingkat"))
    session["tingkat"] = grade
    session.pop("kelas", None)
    session.pop("verified", None)
    return redirect(url_for("kelas_list"))


@app.route("/siswa/kelas")
def kelas_list():
    if session.get("role") != "siswa" or not session.get("tingkat"):
        return redirect(url_for("role"))
    grade = session["tingkat"]
    return render_template(
        "kelas.html",
        grade=grade,
        classes=classes_for_grade(grade),
    )


@app.route("/siswa/kelas/pilih", methods=["POST"])
def pilih_kelas():
    if session.get("role") != "siswa" or not session.get("tingkat"):
        return redirect(url_for("role"))
    kelas = (request.form.get("kelas") or "").strip()
    grade = session["tingkat"]
    if kelas not in classes_for_grade(grade):
        flash("Kelas tidak ditemukan. Silakan pilih lagi.", "error")
        return redirect(url_for("kelas_list"))
    session["kelas"] = kelas
    session.pop("verified", None)
    return redirect(url_for("verify_siswa"))


@app.route("/siswa/verifikasi", methods=["GET", "POST"])
def verify_siswa():
    if (
        session.get("role") != "siswa"
        or not session.get("tingkat")
        or not session.get("kelas")
    ):
        return redirect(url_for("role"))

    if request.method == "POST":
        nama = (request.form.get("nama") or "").strip()
        nisn = _norm_nis(request.form.get("nisn") or request.form.get("nis") or "")
        if not nama or not nisn:
            flash("Mohon isi Nama Lengkap dan NISN.", "error")
            return render_template("verify_siswa.html")

        student = find_student(session["tingkat"], session["kelas"], nama, nisn)
        if not student:
            flash(
                "Data tidak cocok. Pastikan Nama Lengkap dan NISN sesuai "
                "dengan data kelas yang dipilih.",
                "error",
            )
            return render_template("verify_siswa.html")

        key = voter_key_siswa(student["nis"])
        if has_voted(key):
            return render_template(
                "already_voted.html",
                nama=student["nama"],
                role_label="Siswa",
            )

        session["voter_nama"] = student["nama"]
        session["voter_nis"] = student["nis"]
        session["voter_key"] = key
        session["verified"] = True
        return redirect(url_for("vote"))

    return render_template("verify_siswa.html")


@app.route("/guru/login", methods=["GET", "POST"])
def guru_login():
    if session.get("role") != "guru":
        session["role"] = "guru"

    if session.get("guru_auth"):
        return redirect(url_for("guru_list"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if check_guru_credentials(username, password):
            session["guru_auth"] = True
            session["role"] = "guru"
            session.permanent = True
            return redirect(url_for("guru_list"))
        flash("Username atau password guru salah. Coba lagi.", "error")
        return render_template("guru_login.html", username=username)

    return render_template("guru_login.html", username="")


@app.route("/guru")
def guru_list():
    if session.get("role") != "guru":
        return redirect(url_for("role"))
    if not require_guru_auth():
        return redirect(url_for("guru_login"))
    return render_template("guru.html", teachers=load_teachers())


@app.route("/guru/pilih", methods=["POST"])
def pilih_guru():
    if session.get("role") != "guru":
        return redirect(url_for("role"))
    if not require_guru_auth():
        return redirect(url_for("guru_login"))

    nama = (request.form.get("nama") or "").strip()
    teacher = find_teacher(nama)
    if not teacher:
        flash("Nama guru tidak ditemukan dalam daftar.", "error")
        return redirect(url_for("guru_list"))

    key = voter_key_guru(teacher)
    if has_voted(key):
        return render_template(
            "already_voted.html",
            nama=teacher,
            role_label="Guru",
        )

    session["voter_nama"] = teacher
    session["voter_nis"] = None
    session["voter_key"] = key
    session["verified"] = True
    return redirect(url_for("vote"))


@app.route("/vote", methods=["GET", "POST"])
def vote():
    if not session.get("verified") or not session.get("voter_key"):
        return redirect(url_for("role"))

    key = session["voter_key"]
    if has_voted(key):
        return render_template(
            "already_voted.html",
            nama=session.get("voter_nama", ""),
            role_label="Siswa" if session.get("role") == "siswa" else "Guru",
        )

    if request.method == "POST":
        try:
            candidate_id = int(request.form.get("candidate_id", 0))
        except (TypeError, ValueError):
            candidate_id = 0

        ok, msg = register_and_vote(
            role=session.get("role", "siswa"),
            key=key,
            nama=session.get("voter_nama", ""),
            candidate_id=candidate_id,
            nis=session.get("voter_nis"),
            kelas=session.get("kelas"),
            tingkat=session.get("tingkat"),
        )
        if not ok:
            if msg == "already":
                return render_template(
                    "already_voted.html",
                    nama=session.get("voter_nama", ""),
                    role_label=(
                        "Siswa" if session.get("role") == "siswa" else "Guru"
                    ),
                )
            flash("Gagal menyimpan suara. Coba lagi.", "error")
            return redirect(url_for("vote"))

        chosen = next((c for c in CANDIDATES if c["id"] == candidate_id), None)
        # Biarkan sesi auth guru agar guru berikutnya di perangkat sama bisa memilih
        keep = session.get("role") == "guru" and session.get("guru_auth")
        clear_flow_session(keep_guru_auth=keep)
        if keep:
            session["role"] = "guru"
        return render_template("success.html", candidate=chosen)

    return render_template(
        "vote.html",
        candidates=CANDIDATES,
        voter_nama=session.get("voter_nama"),
        role=session.get("role"),
    )


@app.route("/hasil")
def hasil():
    board = scoreboard()
    return render_template(
        "hasil.html",
        board=board,
        candidates=board["candidates"],
        total_votes=board["total_votes"],
        bonus_points=BONUS_POINTS,
        bonus_candidate_id=BONUS_CANDIDATE_ID,
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_panel"))

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if check_admin_auth(username, password):
            session["is_admin"] = True
            flash("Berhasil masuk ke Panel Admin OSIS.", "success")
            return redirect(url_for("admin_panel"))
        flash("Username atau password admin tidak sah.", "error")
        return render_template("admin_login.html", username=username)

    return render_template("admin_login.html", username="")


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Anda telah keluar dari Panel Admin.", "info")
    return redirect(url_for("home"))


@app.route("/admin")
@admin_required
def admin_panel():
    stats = participation_stats()
    pending = pending_voters()
    board = scoreboard()
    return render_template(
        "admin.html",
        stats=stats,
        pending_siswa=pending["siswa"],
        pending_guru=pending["guru"],
        board=board,
        bonus_points=BONUS_POINTS,
    )


@app.route("/live-videotron")
def live_videotron():
    """Papan skor bersih untuk layar videotron sekolah."""
    return render_template(
        "live_videotron.html",
        candidates=CANDIDATES,
        bonus_points=BONUS_POINTS,
        bonus_candidate_id=BONUS_CANDIDATE_ID,
    )


@app.route("/api/live-scores")
def api_live_scores():
    board = scoreboard()
    stats = participation_stats()
    return jsonify(
        {
            "ok": True,
            "total_votes": board["total_votes"],
            "bonus_candidate_id": board["bonus_candidate_id"],
            "bonus_points": board["bonus_points"],
            "participation_pct": stats["participation_pct"],
            "voted_total": stats["voted_total"],
            "eligible_total": stats["eligible_total"],
            "candidates": [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "theme": c["theme"],
                    "votes": c["votes"],
                    "bonus": c["bonus"],
                    "final": c["final"],
                }
                for c in board["candidates"]
            ],
        }
    )


# Warm caches at import/startup
with app.app_context():
    init_db()
    load_students()
    load_teachers()
    total_eligible()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
