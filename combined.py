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

scope = ("playlist-read-private playlist-modify-private playlist-modify-public "
         "user-read-playback-state user-read-currently-playing user-top-read "
         "user-library-read user-follow-read")

# ---------------- Flask App Setup ----------------
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
combined_file = "combined_data.json"

spotify_token_info = None
driver_state = "Calm"
last_blink_time = time.time()
eye_closed_start = None
yawns = []
alert_buffer_start = None
alert_blink_count = 0
playlist_created = False

# ---------------- Mediapipe setup ----------------
mp_face_mesh = mp.solutions.face_mesh
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
UPPER_LIP = [13, 14]
LOWER_LIP = [14, 17]

EYE_AR_THRESH = 0.2
DROWSY_EYE_TIME = 5.0
YAWN_LIMIT = 3
MOUTH_OPEN_THRESH = 0.65
YAWN_WINDOW = 10
ALERT_OPEN_TIME = 8.0
ALERT_BUFFER = 5.0

# ---------------- Mood Parameters ----------------
MOOD_PARAMS = {
    "Calm": {"target_energy": 0.3, "target_valence": 0.7, "genres": ["chill", "ambient", "acoustic"],
             "description": "Relaxing and peaceful tracks for calm driving"},
    "Alert": {"target_energy": 0.8, "target_valence": 0.5, "genres": ["electronic", "techno", "drum-and-bass"],
              "description": "High-energy music to keep you awake and focused"},
    "Drowsy": {"target_energy": 0.6, "target_valence": 0.8, "genres": ["indie-pop", "funk", "disco"],
               "description": "Upbeat and positive music to combat drowsiness"}
}

# ---------------- Helpers ----------------
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

def fetch_and_save_spotify_data(sp):
    user_data = {}
    try:
        top_artists_resp = sp.current_user_top_artists(limit=5, time_range='medium_term')
        user_data['top_artists'] = [{"name": a['name'], "id": a['id']} for a in top_artists_resp['items']]
        if os.path.exists(combined_file):
            with open(combined_file, "r") as f:
                combined_data = json.load(f)
        else:
            combined_data = {}
        combined_data['spotify'] = user_data
        with open(combined_file, "w") as f:
            json.dump(combined_data, f, indent=4)
    except Exception as e:
        print(f"Error fetching Spotify data: {e}")

# ---------------- Playlist Generation ----------------
# ---------------- Playlist Generation (Updated) ----------------
def get_mood_recommendations_50_50(sp, user_data, mood, total_tracks=15):
    mood_params = MOOD_PARAMS[mood]

    # Get valid Spotify genres
    try:
        valid_genres = sp.recommendation_genre_seeds()
    except Exception as e:
        print(f"Error fetching valid genres: {e}")
        valid_genres = []

    seed_genres = [g for g in mood_params['genres'] if g in valid_genres]
    if not seed_genres:
        print(f"No valid genres for mood '{mood}', using fallback genres")
        seed_genres = valid_genres[:3]  # pick first 3 valid genres as fallback

    # Top artist tracks (familiar)
    known_tracks = []
    try:
        market = sp.current_user().get('country', 'US')
    except Exception:
        market = 'US'

    for artist in user_data.get('top_artists', []):
        try:
            top_tracks = sp.artist_top_tracks(artist['id'], country=market)['tracks']
            for t in top_tracks[:2]:
                known_tracks.append(t['uri'])
        except Exception:
            continue

    # Discovery tracks
    discovery_tracks = []
    random.shuffle(seed_genres)
    for genre in seed_genres:
        if len(discovery_tracks) >= total_tracks // 2:
            break
        try:
            recs = sp.recommendations(seed_genres=[genre], limit=total_tracks // 2, market=market)['tracks']
            discovery_tracks += [t['uri'] for t in recs]
        except Exception as e:
            print(f"Failed to fetch recommendations for genre {genre}: {e}")

    # Fallback if still not enough discovery tracks
    if len(discovery_tracks) < total_tracks // 2:
        fallback_playlists = {
            "Calm": ["37i9dQZF1DX4sWSpwq3LiO"],
            "Alert": ["37i9dQZF1DX0BcQWzuB7ZO"],
            "Drowsy": ["37i9dQZF1DX9tPFwDMOaN1"]
        }
        fallback_id = fallback_playlists.get(mood)
        if fallback_id:
            try:
                items = sp.playlist_items(fallback_id, market=market, limit=(total_tracks // 2 - len(discovery_tracks)))['items']
                discovery_tracks += [i['track']['uri'] for i in items if i['track']]
            except Exception:
                pass

    # Combine known + discovery tracks (50/50)
    half_known = min(len(known_tracks), total_tracks // 2)
    half_discovery = min(len(discovery_tracks), total_tracks - half_known)
    playlist_tracks = known_tracks[:half_known] + discovery_tracks[:half_discovery]

    # Fill remaining if less than total_tracks
    remaining = total_tracks - len(playlist_tracks)
    if remaining > 0:
        extra_tracks = (known_tracks[half_known:] + discovery_tracks[half_discovery:])[:remaining]
        playlist_tracks += extra_tracks

    return playlist_tracks

def create_smart_playlist_fixed(sp, total_tracks=15):
    global created_playlist_id, playlist_created, driver_state

    try:
        user_id = sp.current_user()['id']
    except Exception as e:
        print(f"Error fetching user: {e}")
        return

    state = driver_state
    mood_params = MOOD_PARAMS[state]
    playlist_name = f"Drive Mood – {state} Mode"

    # Delete previous playlist
    if created_playlist_id:
        try:
            sp.current_user_unfollow_playlist(created_playlist_id)
        except Exception:
            pass

    # Create playlist
    try:
        playlist = sp.user_playlist_create(user=user_id, name=playlist_name, public=False,
                                           description=mood_params['description'])
        created_playlist_id = playlist['id']
    except Exception as e:
        print(f"Error creating playlist: {e}")
        return

    # Load Spotify data
    if not os.path.exists(combined_file):
        print("No Spotify data available.")
        return
    with open(combined_file, "r") as f:
        combined_data = json.load(f)
    spotify_data = combined_data.get("spotify", {})

    # Generate playlist tracks
    track_uris = get_mood_recommendations_50_50(sp, spotify_data, state, total_tracks=total_tracks)
    if track_uris:
        try:
            sp.playlist_add_items(created_playlist_id, track_uris)
            print(f"✅ Created '{playlist_name}' with {len(track_uris)} tracks")
            playlist_created = True
        except Exception as e:
            print(f"Error adding tracks: {e}")
    else:
        print("❌ No tracks found for playlist")


# ---------------- Driver Monitoring ----------------
def monitor_driver():
    global driver_state, last_blink_time, eye_closed_start, yawns
    global alert_buffer_start, alert_blink_count, playlist_created, monitoring_active

    cap = cv2.VideoCapture(0)
    start_time = time.time()
    last_ear = 0.3

    with mp_face_mesh.FaceMesh(max_num_faces=1, refine_landmarks=True,
                               min_detection_confidence=0.5, min_tracking_confidence=0.5) as face_mesh:
        while monitoring_active and cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            h, w, _ = frame.shape
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)
            now = time.time()
            new_state = driver_state
            drowsy = False

            if results.multi_face_landmarks:
                face_landmarks = results.multi_face_landmarks[0]
                left_ear = eye_aspect_ratio(face_landmarks.landmark, LEFT_EYE, w, h)
                right_ear = eye_aspect_ratio(face_landmarks.landmark, RIGHT_EYE, w, h)
                ear = (left_ear + right_ear) / 2.0

                if ear < EYE_AR_THRESH and last_ear >= EYE_AR_THRESH:
                    if eye_closed_start is None:
                        eye_closed_start = now
                elif ear >= EYE_AR_THRESH and last_ear < EYE_AR_THRESH:
                    if eye_closed_start is not None:
                        blink_duration = now - eye_closed_start
                        if 0.1 < blink_duration < 1.0:
                            last_blink_time = now
                        eye_closed_start = None

                last_ear = ear
                if ear < EYE_AR_THRESH and eye_closed_start and now - eye_closed_start >= DROWSY_EYE_TIME:
                    drowsy = True

                mouth_ratio = mouth_open_ratio(face_landmarks.landmark, w, h)
                if mouth_ratio > MOUTH_OPEN_THRESH:
                    yawns.append(now)
                    yawns = [y for y in yawns if now - y < YAWN_WINDOW]
                if len(yawns) >= YAWN_LIMIT:
                    drowsy = True

            if drowsy:
                new_state = "Drowsy"
            elif driver_state == "Alert" and new_state == "Alert":
                pass

            if new_state != driver_state:
                driver_state = new_state
                update_json()

            if now - start_time >= 30 and not playlist_created:
                sp = get_spotify_client()
                if sp:
                    create_smart_playlist_fixed(sp, total_tracks=15)
                    monitoring_active = False

            cv2.putText(frame, f"State: {driver_state}", (30, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
            if not playlist_created:
                time_left = max(0, 30 - (now - start_time))
                cv2.putText(frame, f"Playlist in: {time_left:.1f}s", (30, 90),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.imshow("Driver Monitor", frame)
            if cv2.waitKey(5) & 0xFF == 27:
                monitoring_active = False
                break
    cap.release()
    cv2.destroyAllWindows()

def delete_playlist(sp):
    global created_playlist_id
    if created_playlist_id:
        try:
            sp.current_user_unfollow_playlist(created_playlist_id)
            created_playlist_id = None
        except Exception:
            pass

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
    global monitoring_thread, monitoring_active, spotify_token_info
    global driver_state, last_blink_time, eye_closed_start, yawns
    global alert_buffer_start, alert_blink_count, playlist_created

    driver_state = "Calm"
    last_blink_time = time.time()
    eye_closed_start = None
    yawns = []
    alert_buffer_start = None
    alert_blink_count = 0
    playlist_created = False

    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())
    if spotify_token_info is None:
        spotify_token_info = cache_handler.get_cached_token()

    fetch_and_save_spotify_data(sp)
    if not monitoring_active:
        monitoring_active = True
        monitoring_thread = threading.Thread(target=monitor_driver)
        monitoring_thread.start()
    return redirect(url_for("home"))

@app.route("/stop", methods=["POST"])
def stop():
    global monitoring_active, playlist_created
    sp = get_spotify_client()
    if monitoring_active:
        monitoring_active = False
        if monitoring_thread:
            monitoring_thread.join(timeout=1)
    delete_playlist(sp)
    playlist_created = False
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True)
