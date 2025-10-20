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
    api_key = "YOUR_OPENWEATHER_API_KEY"  # replace to enable
    if not api_key or api_key == "YOUR_OPENWEATHER_API_KEY":
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

def get_environment_conditions(lux=None, now=None, speed_kmh=None):
    """
    Determine environment descriptors based on:
    - lux (lighting)
    - time of day
    - speed (km/h)
    Returns a dict with time_of_day, light_condition, speed_condition, and mood_keywords
    """
    import datetime as dt
    if now is None:
        now = dt.datetime.now()
    hour = now.hour

    # time of day coarse categories
    if 5 <= hour < 12:
        time_of_day = "morning"
    elif 12 <= hour < 17:
        time_of_day = "afternoon"
    elif 17 <= hour < 21:
        time_of_day = "evening"
    else:
        time_of_day = "night"

    # interpret lux
    if lux is None:
        lux = 300.0
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

    # interpret speed
    if speed_kmh is None:
        speed_kmh = 0
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

    # keywords for lighting
    if light_condition == "bright":
        mood_keywords = ["energetic", "upbeat", "bright", "dance"]
    elif light_condition == "dim":
        mood_keywords = ["focus", "groove", "midtempo"]
    else:
        mood_keywords = ["warm", "cozy", "soft"]

    # slight time-of-day adjustment
    if time_of_day == "night":
        mood_keywords += ["chill", "ambient"]

    # add speed-related influence
    if speed_condition == "fast":
        mood_keywords += ["high tempo", "intense", "driving", "bass"]
    elif speed_condition == "moderate":
        mood_keywords += ["steady", "groovy", "balanced"]
    else:
        mood_keywords += ["relaxed", "mellow"]

    return {
        "time_of_day": time_of_day,
        "light_condition": light_condition,
        "speed_condition": speed_condition,
        "mood_keywords": list(dict.fromkeys(mood_keywords))
    }



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

# ---------------- Playlist creation ----------------
def create_smart_playlist_fixed(sp, total_tracks=40, env_lux=None):
    """
    Creates a playlist according to global driver_state. Uses weather and
    environment (lux/time) to augment keywords. env_lux is optional (simulate a light sensor).
    Returns created playlist id or None.
    """
    global created_playlist_id, playlist_created, driver_state

    state = driver_state  # local alias
    mood_params = MOOD_PARAMS.get(state, {"description": ""})
    # ---- Ask user for simulated lux input ----
    try:
        lux_input = float(input("💡 Enter ambient lux value (e.g., 50=dark, 300=dim, 1000=bright): "))
    except Exception:
        lux_input = None

# ---- Get environment conditions ----
    env = get_environment_conditions(lux_input)
    print(f"🌤️ Environment detected: {env['time_of_day']} | {env['light_condition']} | Lux={lux_input}")

    # ---- Ask user for simulated driving speed ----
    try:
        speed_input = float(input("🚗 Enter simulated driving speed (km/h): "))
    except Exception:
        speed_input = 0

# ---- Get environment conditions ----
    env = get_environment_conditions(lux_input, speed_kmh=speed_input)
    print(f"🌤️ Environment detected: {env['time_of_day']} | {env['light_condition']} | {env['speed_condition']} | Lux={lux_input} | Speed={speed_input} km/h")


    playlist_name = f"Drive Mood – {state} Mode – {int(time.time())}"

    # create playlist shell
    try:
        user_id = sp.current_user()["id"]
        playlist = sp.user_playlist_create(
            user=user_id, name=playlist_name, public=False, description=mood_params["description"]
        )
        created_playlist_id = playlist["id"]
        print(f"🎶 Created playlist shell: {playlist_name} ({created_playlist_id})")
    except Exception as e:
        print(f"Error creating playlist: {e}")
        return None

    # fetch user's top tracks & artists
    try:
        top_tracks_full = sp.current_user_top_tracks(limit=50, time_range="medium_term")["items"]
        top_tracks = [t for t in top_tracks_full if t and t.get("uri")]
    except Exception as e:
        print(f"Error fetching top tracks: {e}")
        top_tracks_full, top_tracks = [], []

    try:
        top_artists_full = sp.current_user_top_artists(limit=20, time_range="medium_term")["items"]
        top_artists = [a["id"] for a in top_artists_full if a.get("id")]
    except Exception as e:
        top_artists_full, top_artists = [], []

    print(f"Found {len(top_tracks)} top tracks, {len(top_artists)} top artists")

    # gather user genres
    user_genres = set()
    try:
        for artist in top_artists_full:
            for g in artist.get("genres", []):
                user_genres.add(g.lower())
        user_genres = list(user_genres)
    except Exception:
        user_genres = []

    # weather keywords (optional)
    weather = get_weather_data()  # returns None or dict
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

    # environment (lux/time) keywords
    env = get_environment_conditions(lux=env_lux)
    env_keywords = env.get("mood_keywords", [])

    # base mood keywords from your SEARCH_KEYWORDS
    base_keywords = SEARCH_KEYWORDS.get(state, [])

    # context-based adjustment to bias for energy/valence where necessary
    context_keywords = []
    if state == "Alert":
        if env["light_condition"] == "bright":
            context_keywords += ["energetic", "dance", "rock", "upbeat", "power"]
        else:
            context_keywords += ["focus", "trap", "groove", "bass"]
    elif state == "Drowsy":
        if env["light_condition"] == "bright":
            context_keywords += ["lively", "positive", "pop", "funk"]
        else:
            context_keywords += ["uplifting", "warm", "soulful"]
    else:  # Calm
        context_keywords += ["chill", "acoustic", "lofi", "mellow"]

    # final keyword blend, deduped and limited
    keywords = list(dict.fromkeys(SEARCH_KEYWORDS.get(state, []) + env["mood_keywords"] + weather_keywords))
    print(f"🔎 Using keywords: {keywords[:10]}")


    # discovery (use your get_discovery_tracks function above)
    discovery_tracks_full = get_discovery_tracks(sp, state, user_genres, max_tracks=int(total_tracks * 1.5))
    print(f"🎧 Found {len(discovery_tracks_full)} genre-matched discovery tracks for {state}")

    # recommendations (seed from user's top tracks/artists)
    rec_tracks_full = []
    try:
        seed_tracks = [t.get("id") for t in top_tracks_full[:5] if t.get("id")]
        seed_artists = list(dict.fromkeys(top_artists))[:5]
        if seed_tracks or seed_artists:
            rec_kwargs = {"limit": min(40, total_tracks)}
            if seed_tracks:
                rec_kwargs["seed_tracks"] = seed_tracks[:3]
            if seed_artists:
                rec_kwargs["seed_artists"] = seed_artists[:2]
            rec_resp = sp.recommendations(**rec_kwargs)
            rec_tracks_full = rec_resp.get("tracks", []) if rec_resp else []
            print(f"✅ Got {len(rec_tracks_full)} recommendations for {state}.")
        else:
            print("⚠️ Not enough seeds for recommendations, skipping recs.")
    except Exception as e:
        print(f"Error fetching recommendations: {e}")
        rec_tracks_full = []

    # combine weighted and shuffle
    random.shuffle(discovery_tracks_full)
    random.shuffle(top_tracks)
    random.shuffle(rec_tracks_full)

    num_discovery = int(total_tracks * 0.7)
    num_top = int(total_tracks * 0.15)
    num_rec = total_tracks - num_discovery - num_top

    combined = discovery_tracks_full[:num_discovery] + top_tracks[:num_top] + rec_tracks_full[:num_rec]
    random.shuffle(combined)  # mix them up so they are not in blocks

    # dedupe & collect URIs
    seen, unique_uris = set(), []
    for track in combined:
        if not track:
            continue
        name = (track.get("name") or "").lower().strip()
        artist = (track["artists"][0]["name"] if track.get("artists") else "").lower().strip()
        key = f"{name}-{artist}"
        uri = track.get("uri")
        if key and uri and key not in seen:
            seen.add(key)
            unique_uris.append(uri)
        if len(unique_uris) >= total_tracks:
            break

    # filler fallback
    if len(unique_uris) < total_tracks:
        filler = discovery_tracks_full + rec_tracks_full + top_tracks
        for t in filler:
            if not t or not t.get("uri"):
                continue
            name = (t.get("name") or "").lower().strip()
            artist = (t["artists"][0]["name"] if t.get("artists") else "").lower().strip()
            key = f"{name}-{artist}"
            if key not in seen:
                seen.add(key)
                unique_uris.append(t["uri"])
            if len(unique_uris) >= total_tracks:
                break

    if not unique_uris:
        print("⚠️ No valid tracks found to add.")
        return created_playlist_id

    # add to playlist (chunks)
    try:
        for i in range(0, len(unique_uris), 100):
            chunk = unique_uris[i:i + 100]
            sp.playlist_add_items(created_playlist_id, chunk)
            print(f"➕ Added {len(chunk)} tracks to playlist chunk ({i}..{i+len(chunk)})")
            time.sleep(0.2)

        print(f"✅ Created '{playlist_name}' with {len(unique_uris)} tracks.")

        # ▶️ Automatically start playback after creation
        start_spotify_playback(sp, created_playlist_id)

    except Exception as e:
        print(f"Error adding tracks: {e}")

    return created_playlist_id



def start_spotify_playback(sp, playlist_id):
    """
    Starts playback of the given Spotify playlist on the user's active device.
    """
    try:
        # Get list of user devices
        devices = sp.devices().get("devices", [])
        if not devices:
            print("⚠️ No active Spotify devices found. Open Spotify on one of your devices and try again.")
            return
        
        # Use the first active device (you can improve this by picking by name/type)
        device_id = devices[0]["id"]

        # Start playback
        sp.start_playback(device_id=device_id, context_uri=f"spotify:playlist:{playlist_id}")
        print(f"🎶 Now playing your playlist on device: {devices[0]['name']}")
    except Exception as e:
        print(f"❌ Could not start playback: {e}")

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
