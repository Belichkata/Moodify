import os
import json
import threading
import time
import cv2
import mediapipe as mp
import math
import random
import requests
from datetime import datetime 
import datetime as dt
from flask import Flask, session, redirect, url_for, request, render_template_string
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler

# ---------------- Spotify Credentials ----------------
CLIENT_ID = "edb8e43341cd46eb8c240d3bfd01e590"
CLIENT_SECRET = "49dba5129cdd414187ac758a53c2b7f4"
REDIRECT_URI = "http://127.0.0.1:5000/callback"

SCOPE = (
    "playlist-read-private playlist-modify-private playlist-modify-public "
    "user-read-playback-state user-read-currently-playing user-top-read "
    "user-library-read user-follow-read user-modify-playback-state"
)


# ---------------- Flask Setup ----------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(64)

cache_handler = FlaskSessionCacheHandler(session)
sp_oauth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
    cache_handler=cache_handler,
    show_dialog=True,
)

# ---------------- Global State ----------------
monitoring_thread = None
monitoring_active = False
playlist_created = False
created_playlist_id = None
spotify_token_info = None
driver_state = "Calm"
combined_file = "combined_data.json"

# ---------------- Mediapipe ----------------
mp_face_mesh = mp.solutions.face_mesh
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
UPPER_LIP = [13, 14]
LOWER_LIP = [14, 17]
EYE_AR_THRESH = 0.3
MOUTH_OPEN_THRESH = 0.65

# ---------------- Mood Config ----------------
MOOD_PARAMS = {
    "Calm": {"description": "Relaxing and peaceful tracks for calm driving"},
    "Alert": {"description": "High-energy music to keep you awake and focused"},
    "Drowsy": {"description": "Upbeat and positive music to combat drowsiness"},
}

MOOD_GENRES = {
    "Calm": ["chill", "lo-fi", "acoustic", "indie", "r&b", "ballad"],
    "Alert": ["hip-hop", "rap", "trap", "edm", "rock", "dance"],
    "Drowsy": ["pop", "k-pop", "dance", "funk", "synthpop", "electronic"],
}

SEARCH_KEYWORDS = {
    "Calm": ["chill k-pop", "melodic rap chill", "soft k-rap", "relaxing k-pop", "ambient noise", "mellow melodic rap"],
    "Alert": ["k-pop hype", "k-rap energy", "rap workout", "fast k-pop"],
    "Drowsy": ["upbeat k-pop", "dance k-pop", "catchy melodic rap", "rap bangers", "noise music upbeat"],
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
    """Writes current driver_state to combined_data.json"""
    global driver_state
    data = {"driver": {"state": driver_state}}
    try:
        if os.path.exists(combined_file):
            with open(combined_file, "r") as f:
                old = json.load(f)
        else:
            old = {}
        old.update(data)
        with open(combined_file, "w") as f:
            json.dump(old, f, indent=4)
    except Exception as e:
        print(f"Error writing json: {e}")

# ---------------- Spotify Helpers ----------------
def get_spotify_client():
    global spotify_token_info
    if spotify_token_info is None:
        return None
    try:
        if sp_oauth.is_token_expired(spotify_token_info):
            spotify_token_info = sp_oauth.refresh_access_token(spotify_token_info["refresh_token"])
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return None
    return Spotify(auth=spotify_token_info["access_token"])

def get_weather_data():
    """
    Optional weather fetch — set your API key if you want weather-influenced keywords.
    """
    api_key = "eafc9b5cf57e96256a1b488d7f84b673"  # replace to enable
    if not api_key or api_key == "eafc9b5cf57e96256a1b488d7f84b673":
        return None
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q=Sofia,BG&appid={api_key}&units=metric"
        r = requests.get(url, timeout=6)
        d = r.json()
        if d.get("cod") != 200:
            return None
        return {"temp": d["main"]["temp"], "condition": d["weather"][0]["main"].lower()}
    except Exception as e:
        print(f"Weather fetch error: {e}")
        return None

import requests

def get_surroundings_from_coords(lat, lon):
    """
    Get location details from Geoapify Reverse Geocoding.
    Returns city, state, country based on coordinates.
    """
    api_key = "96996f00c3bd49f8a1b5b85195480367"  
    url = f"https://api.geoapify.com/v1/geocode/reverse?lat={lat}&lon={lon}&apiKey={api_key}"
    try:
        r = requests.get(url, timeout=6)
        data = r.json()
        if "features" in data and data["features"]:
            props = data["features"][0]["properties"]
            city = props.get("city") or props.get("town") or props.get("village") or "Unknown"
            state = props.get("state") or "Unknown"
            country = props.get("country") or "Unknown"
            print(f"📍 Location detected: {city}, {state}, {country}")
            return {"city": city, "state": state, "country": country}
        else:
            print("⚠️ No location features found for these coordinates.")
            return {"city": "Unknown", "state": "Unknown", "country": "Unknown"}
    except Exception as e:
        print(f"❌ Error fetching surroundings: {e}")
        return {"city": "Unknown", "state": "Unknown", "country": "Unknown"}


# ---------- Discovery helper placed at module scope ----------
def get_discovery_tracks(sp, mood, user_genres, max_tracks=400):
    """
    Collect many candidates from playlists matching keywords, batch-fetch artist genres,
    fuzzy-match against user_genres, and fallback to random selection so we never return empty.
    """
    discovery_tracks = []
    keywords = SEARCH_KEYWORDS.get(mood, ["chill"])
    all_tracks = []

    # gather candidate tracks from playlists matching keywords
    for keyword in keywords:
        try:
            results = sp.search(q=f"{keyword} playlist", type="playlist", limit=10)
            playlists = results.get("playlists", {}).get("items", [])
        except Exception as e:
            print(f"Search failed for '{keyword}': {e}")
            playlists = []
        for pl in playlists:
            try:
                items = sp.playlist_items(pl["id"], limit=80)["items"]
                for i in items:
                    track = i.get("track")
                    if track and track.get("uri"):
                        all_tracks.append(track)
                    if len(all_tracks) >= max_tracks * 2:
                        break
                if len(all_tracks) >= max_tracks * 2:
                    break
            except Exception as e:
                # ignore single-playlist failures
                continue
        if len(all_tracks) >= max_tracks * 2:
            break

    print(f"🎧 Gathered {len(all_tracks)} discovery candidates for {mood}")

    if not all_tracks:
        return []

    # Batch-fetch unique artist genres
    artist_cache = {}
    unique_artist_ids = list({t["artists"][0]["id"] for t in all_tracks if t.get("artists") and t["artists"][0].get("id")})
    for i in range(0, len(unique_artist_ids), 50):
        batch = unique_artist_ids[i:i+50]
        try:
            res = sp.artists(batch).get("artists", [])
            for artist in res:
                artist_cache[artist["id"]] = [g.lower() for g in artist.get("genres", [])]
        except Exception as e:
            # if artists endpoint fails for a batch, skip it
            print(f"⚠️ Genre batch fetch failed ({i // 50}): {e}")

    # fuzzy match helper
    def fuzzy_genre_match(artist_genres, user_genres):
        if not artist_genres or not user_genres:
            return False
        for ag in artist_genres:
            for ug in user_genres:
                if ug in ag or ag in ug:
                    return True
        return False

    # apply filter — but keep track of candidates
    for track in all_tracks:
        try:
            aid = track["artists"][0]["id"]
            ag = artist_cache.get(aid, [])
            if fuzzy_genre_match(ag, user_genres):
                discovery_tracks.append(track)
            if len(discovery_tracks) >= max_tracks:
                break
        except Exception:
            continue

    # fallback: if nothing matched, pick random from candidates (so playlist won't be empty)
    if not discovery_tracks:
        take = min(len(all_tracks), max_tracks)
        discovery_tracks = random.sample(all_tracks, take)

    print(f"✅ Using {len(discovery_tracks)} discovery tracks after filtering for {mood}")
    return discovery_tracks


def get_traffic_status(lat, lon, current_speed, tomtom_key):
    """
    Compare the driver's speed to TomTom traffic data and infer traffic condition.
    Returns: 'heavy', 'moderate', or 'free' (for traffic level).
    """
    url = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/50/json"
    params = {"point": f"{lat},{lon}", "key": tomtom_key}

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ TomTom API error {response.status_code}: {response.text}")
            return "unknown"

        data = response.json()
        segment = data.get("flowSegmentData")
        if not segment:
            print("❌ No flowSegmentData returned.")
            return "unknown"

        free_flow = segment.get("freeFlowSpeed", 0)
        print(f"TomTom free flow speed: {free_flow} km/h | Your speed: {current_speed} km/h")

        # Compare current driving speed with free-flow
        if current_speed < free_flow * 0.4:
            print("🚗 Heavy traffic detected.")
            return "heavy"
        elif current_speed < free_flow * 0.8:
            print("🚙 Moderate traffic.")
            return "moderate"
        else:
            print("🏎️ Free-flowing traffic.")
            return "free"
    except Exception as e:
        print(f"❌ Error checking TomTom traffic: {e}")
        return "unknown"


def get_environment_conditions(lux=None, now=None, speed_kmh=None):
    """
    Determine environment descriptors based on:
    - lux (lighting)
    - time of day
    - speed (km/h)
    Returns a dict with time_of_day, light_condition, speed_condition, and mood_keywords
    """
    if now is None:
        now = dt.datetime.now()
    hour = now.hour

    # Time of day
    if 5 <= hour < 12:
        time_of_day = "morning"
    elif 12 <= hour < 17:
        time_of_day = "afternoon"
    elif 17 <= hour < 21:
        time_of_day = "evening"
    else:
        time_of_day = "night"

    # Lighting
    if lux is None:
        lux_val = 300.0
    else:
        try:
            lux_val = float(lux)
        except Exception:
            lux_val = 300.0

    if lux_val >= 800:
        light_condition = "bright"
    elif lux_val >= 200:
        light_condition = "dim"
    else:
        light_condition = "dark"

    # Speed
    if speed_kmh is None:
        speed_val = 0
    else:
        try:
            speed_val = float(speed_kmh)
        except Exception:
            speed_val = 0

    if speed_val >= 100:
        speed_condition = "fast"
    elif speed_val >= 40:
        speed_condition = "moderate"
    else:
        speed_condition = "slow"

    # Mood keywords based on light and speed
    mood_keywords = []
    if light_condition == "bright":
        mood_keywords += ["energetic", "upbeat", "bright"]
    elif light_condition == "dim":
        mood_keywords += ["focus", "groove", "midtempo"]
    else:
        mood_keywords += ["warm", "cozy", "soft"]

    if speed_condition == "fast":
        mood_keywords += ["driving", "intense"]
    elif speed_condition == "moderate":
        mood_keywords += ["steady", "balanced"]
    else:
        mood_keywords += ["relaxed", "mellow"]

    if time_of_day == "night":
        mood_keywords += ["chill", "ambient"]

    return {
        "time_of_day": time_of_day,
        "light_condition": light_condition,
        "speed_condition": speed_condition,
        "mood_keywords": list(dict.fromkeys(mood_keywords))
    }


def start_spotify_playback(sp, playlist_id):
    """
    Start playback of the specified playlist on the user's active Spotify device.
    """
    try:
        devices = sp.devices()
        if not devices['devices']:
            print("⚠️ No active Spotify device found. Open Spotify on one of your devices and try again.")
            return

        # Pick the first active device
        device_id = devices['devices'][0]['id']

        # Start playback
        sp.start_playback(device_id=device_id, context_uri=f"spotify:playlist:{playlist_id}")
        print("🎧 Playback started on your active Spotify device.")
    except Exception as e:
        print(f"❌ Could not start playback: {e}")


def create_smart_playlist_fixed(sp, total_tracks=40, env_lux=None):
    """
    Creates a playlist based on driver mood, weather, lighting, speed, and surroundings.
    Automatically plays the playlist and deletes the old one if it exists.
    """
    global created_playlist_id, playlist_created, driver_state

    # ---------------- Ask for user inputs ----------------
    try:
        lux_input = float(input("💡 Enter ambient lux value (e.g., 50=dark, 300=dim, 1000=bright): "))
    except Exception:
        lux_input = 300.0

    try:
        speed_input = float(input("🚗 Enter simulated driving speed (km/h): "))
    except Exception:
        speed_input = 0

    try:
        lat = float(input("🌍 Enter your latitude: "))
        lon = float(input("🌍 Enter your longitude: "))
    except Exception:
        lat, lon = 42.6977, 23.3219  # default: Sofia


    # --- Get live traffic condition ---
    TOMTOM_KEY = "9M7YdaLFAFD06NgSt1Vxwp5ROzZt0dBS"  # <-- Replace with your key
    traffic_status = get_traffic_status(lat, lon, speed_input, TOMTOM_KEY)

# Adjust mood weighting if in traffic
    if traffic_status == "heavy":
        print("🧘 Heavy traffic → shifting toward relaxing and calm tracks.")
        state = "calm"
    elif traffic_status == "moderate" and state == "alert":
        print("🚦 Moderate traffic → blending alert with calm tracks.")
        state = "neutral"

    # ---------------- Get environment and surroundings ----------------
    env = get_environment_conditions(lux_input, speed_kmh=speed_input)
    surroundings = get_surroundings_from_coords(lat, lon)
    print(f"🌤️ Environment: {env['time_of_day']} | {env['light_condition']} | {env['speed_condition']}")
    print(f"📍 Surroundings: {surroundings['city']}, {surroundings['country']}")

    # ---------------- Handle existing playlist ----------------
    if created_playlist_id:
        try:
            sp.current_user_unfollow_playlist(created_playlist_id)
            print(f"🗑️ Deleted old playlist {created_playlist_id}")
        except Exception as e:
            print(f"⚠️ Could not delete old playlist: {e}")
        created_playlist_id = None

    # ---------------- Create new playlist ----------------
    state = driver_state
    mood_params = MOOD_PARAMS.get(state, {"description": ""})
    playlist_name = f"Drive Mood – {state} Mode – {int(time.time())}"

    try:
        user_id = sp.current_user()["id"]
        playlist = sp.user_playlist_create(
            user=user_id, name=playlist_name, public=False, description=mood_params["description"]
        )
        created_playlist_id = playlist["id"]
        print(f"🎶 Created playlist: {playlist_name}")
    except Exception as e:
        print(f"❌ Error creating playlist: {e}")
        return None

    # ---------------- Get user’s top data ----------------
    try:
        top_tracks_full = sp.current_user_top_tracks(limit=50, time_range="medium_term")["items"]
        top_artists_full = sp.current_user_top_artists(limit=20, time_range="medium_term")["items"]
    except Exception:
        top_tracks_full, top_artists_full = [], []

    top_tracks = [t for t in top_tracks_full if t.get("uri")]
    top_artists = [a["id"] for a in top_artists_full if a.get("id")]

    user_genres = []
    for artist in top_artists_full:
        user_genres += [g.lower() for g in artist.get("genres", [])]
    user_genres = list(set(user_genres))

    # ---------------- Weather + Environment Influence ----------------
    weather = get_weather_data() or {}
    weather_keywords = []
    if weather:
        t = weather.get("temp")
        c = weather.get("condition", "")
        if "rain" in c or (t is not None and t < 10):
            weather_keywords = ["rainy day", "cozy", "soft"]
        elif "clear" in c and (t is not None and t > 20):
            weather_keywords = ["sunny", "bright", "energetic"]
        else:
            weather_keywords = ["upbeat", "positive", "chill"]

    surroundings_keywords = []
    if surroundings["country"].lower() in ["greece", "spain", "italy"]:
        surroundings_keywords = ["mediterranean", "sunny", "vibrant"]
    elif surroundings["country"].lower() in ["norway", "sweden", "finland"]:
        surroundings_keywords = ["nordic", "ambient", "chill"]
    elif surroundings["city"].lower() in ["sofia", "paris", "berlin"]:
        surroundings_keywords = ["urban", "modern", "city vibe"]

    # ---------------- Build keyword blend ----------------
    base_keywords = SEARCH_KEYWORDS.get(state, [])
    env_keywords = env.get("mood_keywords", [])
    final_keywords = list(dict.fromkeys(base_keywords + env_keywords + weather_keywords + surroundings_keywords))
    print(f"🔎 Using keywords: {final_keywords[:10]}")

    # ---------------- Fetch discovery & recommendations ----------------
    discovery_tracks_full = get_discovery_tracks(sp, state, user_genres, max_tracks=int(total_tracks * 1.5))
    rec_tracks_full = []
    try:
        seed_tracks = [t.get("id") for t in top_tracks_full[:5] if t.get("id")]
        seed_artists = list(dict.fromkeys(top_artists))[:5]
        rec_resp = sp.recommendations(seed_tracks=seed_tracks[:3], seed_artists=seed_artists[:2], limit=25)
        rec_tracks_full = rec_resp.get("tracks", []) if rec_resp else []
    except Exception:
        pass

    # ---------------- Combine & Deduplicate ----------------
    random.shuffle(discovery_tracks_full)
    random.shuffle(top_tracks)
    random.shuffle(rec_tracks_full)

    combined = discovery_tracks_full[:int(total_tracks * 0.7)] + \
               top_tracks[:int(total_tracks * 0.15)] + \
               rec_tracks_full[:int(total_tracks * 0.15)]
    random.shuffle(combined)

    seen, uris = set(), []
    for t in combined:
        if not t: continue
        name = (t.get("name") or "").lower().strip()
        artist = (t["artists"][0]["name"] if t.get("artists") else "").lower().strip()
        key = f"{name}-{artist}"
        if key not in seen and t.get("uri"):
            seen.add(key)
            uris.append(t["uri"])
        if len(uris) >= total_tracks: break

    # ---------------- Add tracks & start playback ----------------
    try:
        for i in range(0, len(uris), 100):
            sp.playlist_add_items(created_playlist_id, uris[i:i+100])
            time.sleep(0.2)
        print(f"✅ Added {len(uris)} tracks to '{playlist_name}'")
        start_spotify_playback(sp, created_playlist_id)
    except Exception as e:
        print(f"❌ Error adding tracks: {e}")

    playlist_created = True
    return created_playlist_id


# ---------------- Driver Monitoring ----------------
def monitor_driver():
    global driver_state, playlist_created, monitoring_active, created_playlist_id

    cap = cv2.VideoCapture(0)
    driver_state = "Calm"
    eye_closure_events, yawns = [], []
    alert_buffer_start, blink_count_buffer = None, 0
    eye_closed_start, eye_closed_duration = None, 0.0
    monitor_start_time, last_blink_time = time.time(), time.time()

    BLINK_TIME_MAX = 0.5
    DROWSY_EYE_TIME = 5.0
    SEMICLOSED_TIME = 3.0
    NO_BLINK_ALERT_TIME = 8.0
    SEMICLOSED_EAR = 0.22
    CLOSED_EAR = 0.18

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
            blinked = False
            yawned = False
            semi_closed_detected = False
            ear = 0.0

            if results.multi_face_landmarks:
                landmarks = results.multi_face_landmarks[0]
                left_ear = eye_aspect_ratio(landmarks.landmark, LEFT_EYE, w, h)
                right_ear = eye_aspect_ratio(landmarks.landmark, RIGHT_EYE, w, h)
                ear = (left_ear + right_ear) / 2.0

                if ear < CLOSED_EAR:
                    if eye_closed_start is None:
                        eye_closed_start = now
                    eye_closed_duration = now - eye_closed_start
                elif CLOSED_EAR <= ear < SEMICLOSED_EAR:
                    if eye_closed_start is None:
                        eye_closed_start = now
                    eye_closed_duration = now - eye_closed_start
                    if eye_closed_duration >= SEMICLOSED_TIME:
                        semi_closed_detected = True
                else:
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

                mouth_ratio = mouth_open_ratio(landmarks.landmark, w, h)
                if mouth_ratio > MOUTH_OPEN_THRESH:
                    yawns.append(now)
                yawns = [y for y in yawns if now - y < 15]
                if len(yawns) >= 2:
                    yawned = True

            # determine driver state
            if len(eye_closure_events) >= 3 or yawned or semi_closed_detected:
                driver_state = "Drowsy"
            else:
                if driver_state == "Calm" and now - last_blink_time >= NO_BLINK_ALERT_TIME:
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

            # Create playlist and stop monitoring
            if not playlist_created and now - monitor_start_time >= 30:
                sp = get_spotify_client()
                if sp:
                    create_smart_playlist_fixed(sp, total_tracks=40)
                    playlist_created = True
                    monitoring_active = False
                    print("✅ Playlist created — monitoring stopped.")
                    break

            cv2.putText(frame, f"State: {driver_state}", (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0,0,255), 3)
            cv2.putText(frame, f"EAR: {ear:.3f}", (30, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)
            if not playlist_created:
                time_left = max(0, 30 - (now - monitor_start_time))
                cv2.putText(frame, f"Playlist in: {time_left:.1f}s", (30,130), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)

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
    <p style="color: green;">✅ Playlist created and monitoring stopped.</p>
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
            print(f"🗑️ Deleted playlist {created_playlist_id}")
        except Exception as e:
            print(f"Error deleting playlist: {e}")
        created_playlist_id = None
    playlist_created = False
    return redirect(url_for("home"))

# ---------------- Run ----------------
if __name__ == "__main__":
    app.run(debug=True)