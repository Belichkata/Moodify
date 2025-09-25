import os
import random
from collections import Counter
from flask import Flask, session, redirect, url_for, request, render_template_string, jsonify
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# --- Flask setup ---
app = Flask(__name__)
app.secret_key = os.urandom(64)

# --- Spotify API credentials ---
CLIENT_ID = "edb8e43341cd46eb8c240d3bfd01e590"
CLIENT_SECRET = "49dba5129cdd414187ac758a53c2b7f4"
REDIRECT_URI = "http://127.0.0.1:5000/callback"
SCOPE = (
    "playlist-read-private playlist-modify-private playlist-modify-public "
    "user-top-read user-read-playback-state user-read-currently-playing "
    "user-modify-playback-state"
)

# --- Spotify auth manager ---
sp_oauth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
    show_dialog=True
)

# --- Helper: get authenticated Spotify client ---
def get_spotify_client():
    token_info = session.get("token_info", None)
    if not token_info:
        return None
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
        session["token_info"] = token_info
    return spotipy.Spotify(auth=token_info["access_token"])

# --- Routes ---
@app.route("/")
def home():
    sp = get_spotify_client()
    if sp is None:
        return redirect(url_for("login"))
    return redirect(url_for("get_playlists"))

@app.route("/login")
def login():
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    token_info = sp_oauth.get_access_token(code)
    session["token_info"] = token_info
    return redirect(url_for("get_playlists"))

@app.route("/get_playlists")
def get_playlists():
    sp = get_spotify_client()
    if sp is None:
        return redirect(url_for("login"))

    playlists_data = sp.current_user_playlists()
    playlists = playlists_data.get("items", [])
    playlist_info = [(pl["name"], pl["external_urls"]["spotify"]) for pl in playlists]

    html = """
    <h2>Your Playlists</h2>
    {% if playlists %}
    <ul>
    {% for name, url in playlists %}
        <li><a href="{{ url }}" target="_blank">{{ name }}</a></li>
    {% endfor %}
    </ul>
    {% else %}
    <p>You have no playlists yet.</p>
    {% endif %}
    <hr>
    <a href="{{ url_for('moodify') }}">🪄 Moodify (JSON export)</a><br>
    <a href="{{ url_for('logout') }}">Logout</a>
    """
    return render_template_string(html, playlists=playlist_info)

@app.route("/moodify")
def moodify():
    sp = get_spotify_client()
    if sp is None:
        return redirect(url_for("login"))

    # Get top artists
    top_artists_data = sp.current_user_top_artists(limit=10, time_range="short_term")["items"]
    artist_info = []
    genres = []
    for artist in top_artists_data:
        artist_info.append({
            "name": artist["name"],
            "id": artist["id"],
            "genres": artist["genres"]
        })
        genres.extend(artist["genres"])

    # Get top tracks
    top_tracks_data = sp.current_user_top_tracks(limit=20, time_range="short_term")["items"]
    tracks_info = []
    for track in top_tracks_data:
        tracks_info.append({
            "name": track["name"],
            "id": track["id"],
            "artists": [a["name"] for a in track["artists"]],
            "uri": track["uri"]
        })

    # Count most common genres
    genre_counts = Counter(genres)

    # JSON structure
    moodify_data = {
        "top_artists": artist_info,
        "top_tracks": tracks_info,
        "top_genres": genre_counts.most_common(10)
    }

    return jsonify(moodify_data)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# --- Run ---
if __name__ == "__main__":
    app.run(debug=True)
