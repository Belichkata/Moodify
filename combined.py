import os
import json
import threading
import time
import cv2
import mediapipe as mp
import math
import random
from flask import Flask, session, redirect, url_for, request, render_template_string
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler

# ---------------- Spotify Credentials ----------------
client_id = "edb8e43341cd46eb8c240d3bfd01e590"
client_secret = "49dba5129cdd414187ac758a53c2b7f4"
redirect_uri = "http://127.0.0.1:5000/callback"

scope = (
    "playlist-read-private playlist-modify-private playlist-modify-public "
    "user-read-playback-state user-read-currently-playing user-top-read "
    "user-library-read user-follow-read"
)

# ---------------- Flask Setup ----------------
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(64)

cache_handler = FlaskSessionCacheHandler(session)
sp_oauth = SpotifyOAuth(
    client_id=client_id,
    client_secret=client_secret,
    redirect_uri=redirect_uri,
    scope=scope,
    cache_handler=cache_handler,
    show_dialog=True
)

# ---------------- Global State ----------------
monitoring_thread = None
monitoring_active = False
created_playlist_id = None
spotify_token_info = None
driver_state = "Calm"
playlist_created = False
combined_file = "combined_data.json"

# Mediapipe face tracking setup
mp_face_mesh = mp.solutions.face_mesh
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
UPPER_LIP = [13, 14]
LOWER_LIP = [14, 17]
EYE_AR_THRESH = 0.3
DROWSY_EYE_TIME = 5.0
YAWN_LIMIT = 3
MOUTH_OPEN_THRESH = 0.65
YAWN_WINDOW = 10

# ---------------- Mood Parameters ----------------
MOOD_PARAMS = {
    "Calm": {"description": "Relaxing and peaceful tracks for calm driving"},
    "Alert": {"description": "High-energy music to keep you awake and focused"},
    "Drowsy": {"description": "Upbeat and positive music to combat drowsiness"}
}

# ---------------- Mood Genres ----------------
MOOD_GENRES = {
    "Calm": ["chill", "lo-fi", "acoustic", "indie", "r&b", "ballad"],
    "Alert": ["hip-hop", "rap", "trap", "edm", "rock", "dance"],
    "Drowsy": ["pop", "k-pop", "dance", "funk", "synthpop", "electronic"]
}

# ---------------- Mood Keywords ----------------
SEARCH_KEYWORDS = {
    "Calm": ["chill k-pop", "melodic rap chill", "soft k-rap", "relaxing k-pop", "ambient noise", "mellow melodic rap"],
    "Alert": ["k-pop hype", "k-rap energy", "rap workout", "fast k-pop"],
    "Drowsy": ["upbeat k-pop", "dance k-pop", "catchy melodic rap", "rap bangers", "noise music upbeat"]
}

# ---------------- Helper Functions ----------------
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

def update_json():
    data = {"driver": {"state": driver_state}}
    if os.path.exists(combined_file):
        with open(combined_file, "r") as f:
            old = json.load(f)
        old.update(data)
        data = old
    with open(combined_file, "w") as f:
        json.dump(data, f, indent=4)

# ---------------- Spotify Helpers ----------------
def get_spotify_client():
    global spotify_token_info
    if spotify_token_info is None:
        return None
    if sp_oauth.is_token_expired(spotify_token_info):
        try:
            spotify_token_info = sp_oauth.refresh_access_token(spotify_token_info['refresh_token'])
        except Exception as e:
            print(f"Error refreshing token: {e}")
            return None
    return Spotify(auth=spotify_token_info['access_token'])

def fetch_user_top_data(sp):
    try:
        top_artists = sp.current_user_top_artists(limit=5, time_range='medium_term')['items']
        top_artist_ids = [a['id'] for a in top_artists]
        top_genres = [g for a in top_artists for g in a['genres']]
        return top_artist_ids, top_genres
    except Exception as e:
        print(f"Error fetching top artists: {e}")
        return [], []

def create_smart_playlist_fixed(sp, total_tracks=20):
    global created_playlist_id, playlist_created, driver_state
    state = driver_state
    mood_genres = MOOD_GENRES[state]
    mood_params = MOOD_PARAMS[state]
    playlist_name = f"Drive Mood – {state} Mode"

    try:
        user_id = sp.current_user()['id']
        playlist = sp.user_playlist_create(
            user=user_id, name=playlist_name, public=False, description=mood_params['description']
        )
        created_playlist_id = playlist['id']
    except Exception as e:
        print(f"Error creating playlist: {e}")
        return

    # -------- Fetch user's top tracks --------
    try:
        top_tracks_full = sp.current_user_top_tracks(limit=50, time_range='medium_term')['items']
        top_tracks = [t for t in top_tracks_full if t.get('id')]
        top_artists = [t['artists'][0]['id'] for t in top_tracks_full[:5]]
    except Exception as e:
        print(f"Error fetching top tracks: {e}")
        top_tracks, top_artists = [], []

    # -------- More robust genre match --------
    def track_matches_genre(track):
        try:
            artist = track['artists'][0]
            artist_info = sp.artist(artist['id'])
            genres = [g.lower().replace("-", " ") for g in artist_info.get('genres', [])]
            if not genres and state == "Calm":
                return True  # allow genre-less calm artists
            return any(any(mg in g for g in genres) for mg in mood_genres)
        except Exception:
            return False

    # -------- Filter top tracks by mood genre --------
    filtered_top = [t for t in top_tracks if track_matches_genre(t)]

    # -------- Discovery tracks (wider net) --------
    def get_discovery_tracks(sp, mood, max_tracks=50):
        discovery_tracks = []
        keywords = SEARCH_KEYWORDS.get(mood, ["chill"])
        for keyword in keywords:
            try:
                results = sp.search(q=f"{keyword} playlist", type="playlist", limit=5)
                playlists = results.get('playlists', {}).get('items', [])
                for pl in playlists:
                    items = sp.playlist_items(pl['id'], limit=30)['items']
                    for i in items:
                        track = i.get('track')
                        if track and track.get('uri') and track not in discovery_tracks:
                            discovery_tracks.append(track)
                        if len(discovery_tracks) >= max_tracks:
                            return discovery_tracks
            except Exception as e:
                print(f"Error searching {keyword}: {e}")
        return discovery_tracks

    discovery_tracks_full = get_discovery_tracks(sp, state, max_tracks=50)
    filtered_discovery = [t for t in discovery_tracks_full if track_matches_genre(t)]

    # -------- Spotify Recommendations --------
    rec_tracks_full = []
    try:
        seed_tracks = [t.get('id') for t in top_tracks_full[:5] if t.get('id')]
        seed_artists = list(dict.fromkeys(top_artists))[:5]
        combined_seeds = seed_tracks + seed_artists
        random.shuffle(combined_seeds)
        combined_seeds = combined_seeds[:5]
        rec_kwargs = {"limit": 10}
        if seed_tracks:
            rec_kwargs["seed_tracks"] = seed_tracks[:3]
        if seed_artists:
            rec_kwargs["seed_artists"] = seed_artists[:2]
        rec_resp = sp.recommendations(**rec_kwargs)
        rec_tracks_full = rec_resp.get("tracks", [])
        print(f"✅ Got {len(rec_tracks_full)} recommendations for {state}.")
    except Exception as e:
        print(f"Error fetching recommendations: {e}")

    # -------- Combine, deduplicate, and fill gaps --------
    combined = filtered_top[:15] + filtered_discovery[:20] + rec_tracks_full
    seen, unique_tracks = set(), []
    for track in combined:
        if not track:
            continue
        name = track['name'].strip().lower()
        artist = track['artists'][0]['name'].strip().lower()
        key = f"{name}-{artist}"
        if key not in seen:
            seen.add(key)
            unique_tracks.append(track)

    # If we didn’t reach total_tracks, pad with unfiltered tracks
    if len(unique_tracks) < total_tracks:
        filler = (top_tracks_full + discovery_tracks_full + rec_tracks_full)
        for t in filler:
            if not t:
                continue
            name = t['name'].strip().lower()
            artist = t['artists'][0]['name'].strip().lower()
            key = f"{name}-{artist}"
            if key not in seen:
                seen.add(key)
                unique_tracks.append(t)
            if len(unique_tracks) >= total_tracks:
                break

    # Shuffle and trim
    random.shuffle(unique_tracks)
    final_uris = [t['uri'] for t in unique_tracks[:total_tracks]]

    try:
        sp.playlist_add_items(created_playlist_id, final_uris)
        print(f"✅ Created '{playlist_name}' with {len(final_uris)} *unique, mood-matched* tracks.")
    except Exception as e:
        print(f"Error adding tracks: {e}")


# ---------------- Driver Monitoring ----------------
def monitor_driver():
    global driver_state, playlist_created, monitoring_active

    cap = cv2.VideoCapture(0)
    driver_state = "Calm"

    eye_closure_events = []  # for drowsy logic
    yawns = []               # for drowsy logic

    alert_buffer_start = None
    blink_count_buffer = 0

    eye_closed_start = None
    eye_closed_duration = 0.0

    monitor_start_time = time.time()  # 30 s independent timer
    last_blink_time = time.time()     # for Calm → Alert

    # ---- Thresholds ----
    BLINK_TIME_MAX = 0.5          # <0.5s = blink
    DROWSY_EYE_TIME = 5.0         # ≥5s = drowsy closure
    SEMICLOSED_TIME = 3.0         # ≥3s semi-closed = drowsy
    NO_BLINK_ALERT_TIME = 8.0     # ≥8s no blink = alert
    SEMICLOSED_EAR = 0.22         # between 0.15–0.25 indicates droopy
    CLOSED_EAR = 0.18             # fully closed

    with mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    ) as face_mesh:

        while monitoring_active and cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            h, w, _ = frame.shape
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)
            now = time.time()

            blinked = False
            yawned = False
            semi_closed_detected = False

            if results.multi_face_landmarks:
                face_landmarks = results.multi_face_landmarks[0]
                left_ear = eye_aspect_ratio(face_landmarks.landmark, LEFT_EYE, w, h)
                right_ear = eye_aspect_ratio(face_landmarks.landmark, RIGHT_EYE, w, h)
                ear = (left_ear + right_ear) / 2.0

                # ---- Eye logic ----
                if ear < CLOSED_EAR:
                    if eye_closed_start is None:
                        eye_closed_start = now
                    eye_closed_duration = now - eye_closed_start

                elif CLOSED_EAR <= ear < SEMICLOSED_EAR:
                    # semi-closed / droopy eyes
                    if eye_closed_start is None:
                        eye_closed_start = now
                    eye_closed_duration = now - eye_closed_start
                    if eye_closed_duration >= SEMICLOSED_TIME:
                        semi_closed_detected = True
                else:
                    # eyes reopened
                    if eye_closed_start is not None:
                        duration = now - eye_closed_start
                        if duration < BLINK_TIME_MAX:
                            blinked = True
                            last_blink_time = now
                        elif duration >= DROWSY_EYE_TIME:
                            if not eye_closure_events or now - eye_closure_events[-1] > DROWSY_EYE_TIME:
                                eye_closure_events.append(now)
                                eye_closure_events = eye_closure_events[-3:]
                    eye_closed_start = None
                    eye_closed_duration = 0.0

                # ---- Yawn detection ----
                mouth_ratio = mouth_open_ratio(face_landmarks.landmark, w, h)
                if mouth_ratio > MOUTH_OPEN_THRESH:
                    yawns.append(now)
                yawns = [y for y in yawns if now - y < 15]
                if len(yawns) >= 2:
                    yawned = True

            # ---- State logic ----
            if len(eye_closure_events) >= 3 or yawned or semi_closed_detected:
                driver_state = "Drowsy"

            else:
                if driver_state == "Calm":
                    if now - last_blink_time >= NO_BLINK_ALERT_TIME:
                        driver_state = "Alert"
                        alert_buffer_start = now
                        blink_count_buffer = 0

                elif driver_state == "Alert":
                    if blinked:
                        blink_count_buffer += 1
                    if alert_buffer_start is None:
                        alert_buffer_start = now
                    elif now - alert_buffer_start >= 5:
                        if blink_count_buffer >= 2:
                            driver_state = "Calm"
                            last_blink_time = now
                        alert_buffer_start = now
                        blink_count_buffer = 0

            update_json()

            # ---- Playlist logic ----
            if not playlist_created and now - monitor_start_time >= 30:
                sp = get_spotify_client()
                if sp:
                    create_smart_playlist_fixed(sp, total_tracks=20)
                    playlist_created = True
                    monitoring_active = False

            # ---- Display ----
            cv2.putText(frame, f"State: {driver_state}", (30, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
            cv2.putText(frame, f"EAR: {ear:.3f}", (30, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            if not playlist_created:
                time_left = max(0, 30 - (now - monitor_start_time))
                cv2.putText(frame, f"Playlist in: {time_left:.1f}s", (30, 130),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            cv2.imshow("Driver Monitor", frame)
            if cv2.waitKey(5) & 0xFF == 27:
                monitoring_active = False
                break

    cap.release()
    cv2.destroyAllWindows()



# ---------------- Flask Routes ----------------
@app.route("/")
def home():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())
    html = """
    <h1>🚗 Drive Mood</h1>
    <form action="{{ url_for('start') }}" method="post">
        <button type="submit">▶ Start Monitoring</button>
    </form>
    <form action="{{ url_for('stop') }}" method="post">
        <button type="submit">⏹ Stop & Delete Playlist</button>
    </form>
    {% if playlist_created %}
    <p style="color: green;">✅ Playlist created! Monitoring stopped.</p>
    {% endif %}
    """
    return render_template_string(html)

@app.route("/callback")
def callback():
    global spotify_token_info
    token_info = sp_oauth.get_access_token(request.args.get("code"))
    spotify_token_info = token_info
    return redirect(url_for("home"))

@app.route("/start", methods=["POST"])
def start():
    global monitoring_thread, monitoring_active, playlist_created
    playlist_created = False
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())
    if not monitoring_active:
        monitoring_active = True
        monitoring_thread = threading.Thread(target=monitor_driver)
        monitoring_thread.start()
    return redirect(url_for("home"))

@app.route("/stop", methods=["POST"])
def stop():
    global monitoring_active, playlist_created, created_playlist_id
    sp = get_spotify_client()
    if monitoring_active:
        monitoring_active = False
        if monitoring_thread:
            monitoring_thread.join(timeout=1)
    if created_playlist_id and sp:
        try:
            sp.current_user_unfollow_playlist(created_playlist_id)
        except Exception:
            pass
        created_playlist_id = None
    playlist_created = False
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True)
