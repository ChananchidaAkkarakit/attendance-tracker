from flask import Flask, request, jsonify, render_template, send_file
import base64, cv2, numpy as np, os, json, csv, math
from datetime import datetime, time as dtime
from insightface.app import FaceAnalysis

app = Flask(__name__)

# --------------------
# Model (face detect + recognition)
# --------------------
model = FaceAnalysis(name="buffalo_l")              # ชี้โมเดลได้ด้วย root="models"
model.prepare(ctx_id=-1, det_size=(640, 640))       # CPU only + ขนาดตรวจจับ

# --------------------
# In-memory face DB  (code -> np.array embedding)
# --------------------
face_db = {}
DB_PATH = "face_db.json"

def save_face_db():
    serializable = {code: ref.tolist() for code, ref in face_db.items()}
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False)

def load_face_db():
    global face_db
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        face_db = {code: np.array(vec, dtype=np.float32) for code, vec in raw.items()}
load_face_db()

# --------------------
# Geofence config (persist to file) & helpers
# --------------------
SITES_PATH = "allowed_sites.json"
ALLOWED_SITES = {}   # code -> [ [lat, lng, radius_m], ... ]

def load_sites():
    global ALLOWED_SITES
    if os.path.exists(SITES_PATH):
        with open(SITES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        ALLOWED_SITES = {
            code: [(float(a), float(b), float(r)) for a, b, r in points]
            for code, points in raw.items()
        }
    else:
        # ค่า default เผื่อทดสอบ: จุดเดียวรัศมี 200 m (แก้เป็นจุดจริง)
        ALLOWED_SITES = {"default": [(14.040438697809682, 100.73365761380248, 200.0)]}
def save_sites():
    with open(SITES_PATH, "w", encoding="utf-8") as f:
        json.dump(ALLOWED_SITES, f, ensure_ascii=False, indent=2)
load_sites()

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0  # meters
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

def is_within_sites(code, lat, lng, accuracy_m=9999, max_accuracy=5000):
    """
    คืนค่า: (within:boolean, distance_m:float|None, reason:str)
    reason ∈ {"ok","gps_accuracy_poor","no_sites_configured","outside_radius"}
    """
    if accuracy_m and max_accuracy and accuracy_m > max_accuracy:
        return (False, None, "gps_accuracy_poor")

    sites = ALLOWED_SITES.get(code, ALLOWED_SITES.get("default", []))
    if not sites:
        return (False, None, "no_sites_configured")

    best_d = None
    for (s_lat, s_lng, radius_m) in sites:
        d = haversine_m(lat, lng, s_lat, s_lng)
        best_d = d if best_d is None else min(best_d, d)
        if d <= radius_m:
            return (True, d, "ok")
    return (False, best_d, "outside_radius")

# --------------------
# Period helper (เช้า/กลางวัน/บ่าย/เย็น)
# --------------------
def time_period(now=None):
    t = (now or datetime.now()).time()
    def between(a, b): return a <= t < b
    if between(dtime(5, 0), dtime(11, 0)):   return "morning"
    if between(dtime(11, 0), dtime(13, 30)): return "noon"
    if between(dtime(13, 30), dtime(17, 0)): return "afternoon"
    return "evening"

# --------------------
# Attendance log
# --------------------
LOG_PATH = "attendance.csv"

def log_attendance(code, kind, score, lat=None, lng=None, distance_m=None, reason="ok"):
    """
    header: ts,code,type,period,score,lat,lng,distance_m,reason
    """
    new_file = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["ts","code","type","period","score","lat","lng","distance_m","reason"])
        w.writerow([
            datetime.now().isoformat(timespec="seconds"),
            code,
            kind,
            time_period(),
            f"{float(score):.3f}",
            f"{lat:.6f}" if lat is not None else "",
            f"{lng:.6f}" if lng is not None else "",
            f"{float(distance_m):.1f}" if distance_m is not None else "",
            reason
        ])

# --------------------
# Utils
# --------------------
def decode_base64_image(b64_string):
    if b64_string.startswith("data:image"):
        b64_string = b64_string.split(",", 1)[1]
    img_data = base64.b64decode(b64_string)
    img_array = np.frombuffer(img_data, np.uint8)
    return cv2.imdecode(img_array, cv2.IMREAD_COLOR)

# --------------------
# Routes
# --------------------
@app.get("/")
def home():
    return "OK — use /ui for the demo, /api/enroll and /api/recognize for APIs"

@app.get("/ui")
def ui():
    return render_template("ui.html")

# ---- faces (enroll/recognize) ----
@app.post("/api/enroll")
def enroll():
    data = request.get_json(force=True)
    code = (data.get("code") or "").strip()
    images = data.get("images", [])
    if not code or not images:
        return jsonify(ok=False, msg="Missing code/images"), 400

    embeddings = []
    for img_b64 in images:
        img = decode_base64_image(img_b64)
        faces = model.get(img)
        if faces:
            embeddings.append(faces[0].normed_embedding)

    if len(embeddings) < 1:
        return jsonify(ok=False, msg="No faces detected")

    face_db[code] = np.mean(embeddings, axis=0)
    save_face_db()
    return jsonify(ok=True, msg="Enrolled", templates=len(embeddings))

@app.post("/api/recognize")
def recognize():
    data = request.get_json(force=True)
    img_b64 = data.get("image")
    kind = (data.get("type") or "checkin").strip().lower()   # "checkin" | "checkout" | ...
    threshold = float(data.get("threshold") or 0.50)

    # ---- พิกัดจาก client ----
    lat = data.get("lat")
    lng = data.get("lng")
    accuracy = data.get("accuracy")
    if lat is None or lng is None:
        return jsonify(ok=False, msg="Location required"), 400

    if not img_b64:
        return jsonify(ok=False, msg="No image"), 400

    img = decode_base64_image(img_b64)
    faces = model.get(img)
    if not faces:
        return jsonify(ok=False, msg="No face found")

    emb = faces[0].normed_embedding
    best_score, best_code = -1.0, None
    for code, ref in face_db.items():
        s = float(np.dot(emb, ref))
        if s > best_score:
            best_score, best_code = s, code

    matched = best_score >= threshold

    # ---- เช็ก geofence ตาม code ที่รู้จำได้ ----
    within, distance_m, reason = (False, None, "face_not_matched")
    if matched and best_code:
        within, distance_m, reason = is_within_sites(
            best_code, float(lat), float(lng), float(accuracy or 0)
        )

    # ผ่านเงื่อนไข: จำหน้าได้ + อยู่ในรัศมี
    if matched and best_code and within:
        log_attendance(best_code, kind, best_score, float(lat), float(lng), distance_m, reason="ok")
        return jsonify(
            ok=True,
            matched=True,
            name=best_code,
            score=round(best_score, 3),
            threshold=threshold,
            period=time_period(),
            geofence=dict(within=True, distance_m=round(distance_m or 0, 1), reason="ok")
        )

    # ไม่ผ่าน
    return jsonify(
        ok=True,
        matched=matched,
        name=best_code if matched else "Unknown",
        score=round(best_score, 3),
        threshold=threshold,
        period=time_period(),
        geofence=dict(
            within=False,
            distance_m=round(distance_m or -1, 1) if distance_m is not None else None,
            reason=reason
        )
    )

# ---- faces utilities ----
@app.get("/api/faces")
def list_faces():
    return jsonify(ok=True, size=len(face_db), codes=sorted(face_db.keys()))

@app.post("/api/reset")
def reset_db():
    face_db.clear()
    save_face_db()
    return jsonify(ok=True, msg="cleared")

@app.get("/api/attendance.csv")
def download_attendance():
    if not os.path.exists(LOG_PATH):
        with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["ts","code","type","period","score","lat","lng","distance_m","reason"])
    return send_file(LOG_PATH, as_attachment=True, download_name="attendance.csv")

# ---- sites (CRUD เบื้องต้น) ----
@app.get("/api/sites/<code>")
def get_sites(code):
    points = ALLOWED_SITES.get(code) or ALLOWED_SITES.get("default", [])
    return jsonify(ok=True, code=code, sites=points)

@app.post("/api/sites/<code>")
def set_sites(code):
    """
    body: { "sites": [ [lat, lng, radius_m], ... ] }
    """
    data = request.get_json(force=True)
    sites = data.get("sites", [])
    if not isinstance(sites, list) or not sites:
        return jsonify(ok=False, msg="sites required"), 400
    norm = []
    for it in sites:
        if not (isinstance(it, (list, tuple)) and len(it) == 3):
            return jsonify(ok=False, msg="each site = [lat,lng,radius_m]"), 400
        lat, lng, r = float(it[0]), float(it[1]), float(it[2])
        norm.append([lat, lng, r])
    ALLOWED_SITES[code] = norm
    save_sites()
    return jsonify(ok=True, size=len(norm))

# --------------------
# Main
# --------------------
if __name__ == "__main__":
    # เคารพ $PORT เมื่อ deploy (เช่น Cloud Run/Fly/Render)
    port = int(os.environ.get("PORT", 5000))
    # templates/ui.html ต้องมีไฟล์อยู่ในโฟลเดอร์ templates/
    app.run(host="0.0.0.0", port=port, debug=True)
