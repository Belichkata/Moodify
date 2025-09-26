import os
import json
import datetime as dt
import requests
import time
import threading
from collections import Counter
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import cv2
import mediapipe as mp
import math

# ==============================
# --- Spotify & Weather Setup ---
# ==============================

CLIENT_ID = "edb8e43341cd46eb8c240d3bfd01e590"
CLIENT_SECRET = "49dba5129cdd414187ac758a53c2b7f4"
REDIRECT_URI = "http://127.0.0.1:5000/callback"
SCOPE = "user-top-read"

BASE_URL = "http://api.openweathermap.org/data/2.5/weather?"
API_KEY = open('api_key.txt', 'r').read().strip()
CITY = "Sofia"
COMBINED_JSON_FILE = "combined_data.json"

THRESHOLDS = {
    'temp_change': 5,
    'weather_change': True
}

last_weather = None
weather_data = {}
spotify_data = {}
driver_state = "Calm"

# ==============================
# --- Mediapipe Setup ---
# ==============================
mp_face_mesh = mp.solutions.face_mesh
mp_drawing = mp.solutions.drawing_utils

LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
UPPER_LIP = [13, 14]
LOWER_LIP = [14, 17]

EYE_AR_THRESH = 0.2
EYE_CLOSED_TIME = 2.0        # seconds closed -> drowsy
YAWN_LIMIT = 3               # yawns before drowsy
MOUTH_OPEN_THRESH = 0.65
BLINK_INTERVAL_THRESH = 10.0  # secs without blink -> alert

STATE_MIN_DURATION = 5.0     # must stay at least this long
RECOVERY_DURATION = 8.0      # sustained normal behavior before returning to Calm

last_blink_time = time.time()
eye_closed_start = None
yawns = []
last_state_change_time = time.time()
last_recovery_start = None

# ==============================
# --- Helpers ---
# ==============================
def euclidean_distance(p1, p2):
    return math.dist(p1, p2)

def eye_aspect_ratio(landmarks, eye_indices, w, h):
    points = [(int(landmarks[i].x * w), int(landmarks[i].y * h)) for i in eye_indices]
    A = euclidean_distance(points[1], points[5])
    B = euclidean_distance(points[2], points[4])
    C = euclidean_distance(points[0], points[3])
    return (A + B) / (2.0 * C)

def mouth_open_ratio(landmarks, w, h):
    upper_lip_y = landmarks[UPPER_LIP[0]].y * h
    lower_lip_y = landmarks[LOWER_LIP[1]].y * h
    return abs(lower_lip_y - upper_lip_y) / h

# ==============================
# --- Spotify ---
# ==============================
def get_spotify_data():
    global spotify_data
    try:
        sp_oauth = SpotifyOAuth(client_id=CLIENT_ID,
                                client_secret=CLIENT_SECRET,
                                redirect_uri=REDIRECT_URI,
                                scope=SCOPE)
        token_info = sp_oauth.get_cached_token()
        if not token_info:
            auth_url = sp_oauth.get_authorize_url()
            print(f"Please open: {auth_url}")
            token_info = sp_oauth.get_access_token(sp_oauth.get_authorization_code())
        sp = spotipy.Spotify(auth=token_info['access_token'])

        top_artists = sp.current_user_top_artists(limit=20, time_range='short_term')
        genres = []
        artist_info = []
        for artist in top_artists['items']:
            artist_info.append({"name": artist["name"],
                                "id": artist["id"],
                                "genres": artist["genres"]})
            genres.extend(artist['genres'])

        top_tracks_data = sp.current_user_top_tracks(limit=20, time_range="short_term")["items"]
        tracks_info = [{"name": t["name"], "id": t["id"],
                        "artists": [a["name"] for a in t["artists"]],
                        "uri": t["uri"]} for t in top_tracks_data]

        genre_counts = Counter(genres)
        top_5_genres = genre_counts.most_common(5)

        spotify_data = {
            "top_artists": artist_info,
            "top_tracks": tracks_info,
            "top_genres": top_5_genres,
            "last_updated": dt.datetime.now().isoformat()
        }
    except Exception as e:
        spotify_data = {"error": str(e)}

# ==============================
# --- Weather ---
# ==============================
def get_weather():
    url = BASE_URL + "appid=" + API_KEY + "&q=" + CITY + "&units=metric"
    return requests.get(url).json()

def monitor_weather():
    global last_weather, weather_data
    while True:
        try:
            current_weather = get_weather()
            if current_weather['cod'] == 200:
                new_weather_data = {
                    "conditions": current_weather['weather'][0]['description'].title(),
                    "temperature": round(current_weather['main']['temp'], 1),
                    "feels_like": round(current_weather['main']['feels_like'], 1),
                    "weather_main": current_weather['weather'][0]['main'],
                    "last_checked": dt.datetime.now().isoformat(),
                }
                weather_data = new_weather_data
                last_weather = current_weather
                update_combined_json()
            time.sleep(300)
        except:
            time.sleep(300)

# ==============================
# --- JSON Update ---
# ==============================
def update_combined_json():
    combined_data = {
        "last_updated": dt.datetime.now().isoformat(),
        "weather": weather_data,
        "spotify": spotify_data,
        "driver": {"state": driver_state}
    }
    with open(COMBINED_JSON_FILE, 'w') as f:
        json.dump(combined_data, f, indent=2)

# ==============================
# --- Driver State ---
# ==============================
def update_driver_state(drowsy_condition, alert_condition):
    global driver_state, last_state_change_time, last_recovery_start
    now = time.time()
    new_state = driver_state

    if driver_state == "Calm":
        if drowsy_condition:
            new_state = "Drowsy"
        elif alert_condition:
            new_state = "Alert"

    elif driver_state == "Drowsy":
        if now - last_state_change_time >= STATE_MIN_DURATION:
            if not drowsy_condition:
                if last_recovery_start is None:
                    last_recovery_start = now
                if now - last_recovery_start >= RECOVERY_DURATION:
                    new_state = "Calm"
            else:
                last_recovery_start = None

    elif driver_state == "Alert":
        if now - last_state_change_time >= STATE_MIN_DURATION:
            if not alert_condition:
                if last_recovery_start is None:
                    last_recovery_start = now
                if now - last_recovery_start >= RECOVERY_DURATION:
                    new_state = "Calm"
            else:
                last_recovery_start = None

    if new_state != driver_state:
        driver_state = new_state
        last_state_change_time = now
        last_recovery_start = None
        update_combined_json()
        print(f"🚗 Driver state changed: {driver_state}")

# ==============================
# --- Main Driver Monitoring ---
# ==============================
def monitor_driver():
    global last_blink_time, eye_closed_start, yawns
    cap = cv2.VideoCapture(0)

    with mp_face_mesh.FaceMesh(max_num_faces=1,
                               refine_landmarks=True,
                               min_detection_confidence=0.5,
                               min_tracking_confidence=0.5) as face_mesh:
        while cap.isOpened():
            success, frame = cap.read()
            if not success:
                break
            h, w, _ = frame.shape
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb_frame)

            drowsy_condition, alert_condition = False, False

            if results.multi_face_landmarks:
                for face_landmarks in results.multi_face_landmarks:
                    left_ear = eye_aspect_ratio(face_landmarks.landmark, LEFT_EYE, w, h)
                    right_ear = eye_aspect_ratio(face_landmarks.landmark, RIGHT_EYE, w, h)
                    ear = (left_ear + right_ear) / 2.0

                    if ear < EYE_AR_THRESH:
                        if eye_closed_start is None:
                            eye_closed_start = time.time()
                    else:
                        if eye_closed_start:
                            duration = time.time() - eye_closed_start
                            if duration < EYE_CLOSED_TIME:
                                last_blink_time = time.time()
                            eye_closed_start = None

                    LONG_CLOSURE_LIMIT = 2.0    # sec
                    CLOSURE_HISTORY = 60        # sec window
                    CLOSURE_REQUIRED = 2        # number of long closures before "drowsy"

                    long_closures = []  # put this outside the loop (global)

# inside loop:
                    if eye_closed_start and (time.time() - eye_closed_start >= LONG_CLOSURE_LIMIT):
    # register a long closure once per event
                        long_closures.append(time.time())
                        eye_closed_start = None  # reset so it doesn't keep firing

# keep only recent closures
                        long_closures = [t for t in long_closures if time.time() - t < CLOSURE_HISTORY]

# mark drowsy only if there are enough closures
                    if len(long_closures) >= CLOSURE_REQUIRED:
                        drowsy_condition = True


                    mouth_ratio = mouth_open_ratio(face_landmarks.landmark, w, h)
                    if mouth_ratio > MOUTH_OPEN_THRESH:
                        yawns.append(time.time())
                        yawns = [y for y in yawns if time.time() - y < 60]
                    if len(yawns) >= YAWN_LIMIT:
                        drowsy_condition = True

                    if time.time() - last_blink_time >= BLINK_INTERVAL_THRESH:
                        alert_condition = True

                    update_driver_state(drowsy_condition, alert_condition)

                    mp_drawing.draw_landmarks(frame,
                                              face_landmarks,
                                              mp_face_mesh.FACEMESH_TESSELATION,
                                              mp_drawing.DrawingSpec(color=(0,255,0),
                                                                     thickness=1,
                                                                     circle_radius=1))

            cv2.putText(frame, f"Driver State: {driver_state}", (30, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
            cv2.imshow('Driver Monitor', frame)
            if cv2.waitKey(5) & 0xFF == 27:
                break
    cap.release()
    cv2.destroyAllWindows()

# ==============================
# --- Main ---
# ==============================
def main():
    get_spotify_data()
    threading.Thread(target=monitor_weather, daemon=True).start()
    monitor_driver()

if __name__ == "__main__":
    main()
