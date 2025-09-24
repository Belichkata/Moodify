import os
import random
from flask import Flask, request, redirect, url_for, render_template_string
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from openai import OpenAI

# --- Spotify Auth ---
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id="edb8e43341cd46eb8c240d3bfd01e590",
    client_secret="49dba5129cdd414187ac758a53c2b7f4",
    redirect_uri="http://127.0.0.1:5000/callback",
    scope="user-top-read playlist-modify-private playlist-modify-public"
))

client = OpenAI(api_key="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")

app = Flask(__name__)
app.secret_key = os.urandom(32)

# --- HTML Form ---
form_template = """
<!DOCTYPE html>
<html>
<head><title>Driving Condition Playlist</title></head>
<body style="font-family: Arial; margin: 50px;">
    <h1>🚗 Generate a Playlist Based on Conditions</h1>
    <form method="POST">
        <label for="condition">Describe your driving condition:</label><br>
        <textarea name="condition" rows="4" cols="50" placeholder="e.g., Nighttime highway driving with light rain"></textarea><br><br>
        <input type="submit" value="Generate Playlist">
    </form>
    {% if playlist_url %}
        <h2>✅ Playlist Created!</h2>
        <p>Genres used: {{ genres }}</p>
        <p><a href="{{ playlist_url }}" target="_blank">Open Playlist on Spotify</a></p>
    {% endif %}
</body>
</html>
"""

# --- Helper Functions ---
def get_user_top_genres():
    top_artists = sp.current_user_top_artists(limit=20)
    genres = []
    for artist in top_artists['items']:
        genres.extend(artist['genres'])
    return list(set(genres))

def get_related_genres(user_genres):
    prompt = f"""
    The user's top genres are: {user_genres}.
    Suggest 3 genres that are different but complementary — something they might enjoy based on their taste.
    Return them as a JSON list, e.g. ["genre1","genre2","genre3"].
    """
    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a music taste assistant. Only return a JSON list of genres."},
            {"role": "user", "content": prompt}
        ]
    )
    try:
        response = completion.choices[0].message.content.strip()
        return eval(response)
    except:
        return random.sample(user_genres, min(len(user_genres), 3))

def ask_ai_for_genres(condition_description):
    prompt = f"""
    The driver is currently experiencing this scenario: "{condition_description}".
    Suggest 3-4 Spotify music genres that would fit this mood and setting.
    Return them as a JSON list, e.g. ["genre1","genre2","genre3"].
    """
    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful music genre recommender that only returns valid Spotify genres."},
            {"role": "user", "content": prompt}
        ]
    )
    try:
        response = completion.choices[0].message.content.strip()
        return eval(response)
    except:
        return []

def create_condition_playlist(condition_description):
    user_genres = get_user_top_genres()
    ai_genres = ask_ai_for_genres(condition_description)

    prioritized = [g for g in ai_genres if g in user_genres]
    for g in ai_genres:
        if g not in prioritized and len(prioritized) < 3:
            prioritized.append(g)

    if len(prioritized) < 3:
        related = get_related_genres(user_genres)
        for g in related:
            if g not in prioritized and len(prioritized) < 3:
                prioritized.append(g)

    user_id = sp.current_user()['id']
    playlist = sp.user_playlist_create(
        user=user_id,
        name=f"Drive Playlist - {condition_description}",
        public=False
    )

    recommendations = sp.recommendations(seed_genres=prioritized[:3], limit=40)
    track_uris = [track['uri'] for track in recommendations['tracks']]
    sp.playlist_add_items(playlist['id'], track_uris)

    return playlist['external_urls']['spotify'], prioritized[:3]

# --- Routes ---
@app.route("/", methods=["GET", "POST"])
def index():
    playlist_url = None
    genres_used = None
    if request.method == "POST":
        condition = request.form['condition']
        playlist_url, genres_used = create_condition_playlist(condition)
    return render_template_string(form_template, playlist_url=playlist_url, genres=genres_used)

@app.route("/callback")
def callback():
    sp.auth_manager.get_access_token(request.args['code'])
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
