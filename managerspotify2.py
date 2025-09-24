import os
from flask import Flask, session, redirect, url_for, request, render_template_string
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler
from collections import Counter

# --- Spotify API credentials ---
client_id = "edb8e43341cd46eb8c240d3bfd01e590"
client_secret = "49dba5129cdd414187ac758a53c2b7f4"
redirect_uri = "http://127.0.0.1:5000/callback"

# --- Required scopes (added playback-control permissions) ---
scope = (
    "playlist-read-private playlist-modify-private playlist-modify-public "
    "user-read-playback-state user-read-currently-playing user-top-read "
    "user-modify-playback-state"
)

# --- Flask app setup ---
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

# --- Helper function to get authenticated Spotify client ---
def get_spotify_client():
    token_info = cache_handler.get_cached_token()
    if token_info is None or not sp_oauth.validate_token(token_info):
        return None
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        cache_handler.save_token_to_cache(token_info)
    return Spotify(auth=token_info['access_token'])


# --- Routes ---
@app.route("/")
def home():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())
    return redirect(url_for("get_playlists"))


@app.route("/callback")
def callback():
    sp_oauth.get_access_token(request.args.get('code'))
    return redirect(url_for('get_playlists'))


@app.route("/get_playlists")
def get_playlists():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())

    playlists_data = sp.current_user_playlists()
    playlists = playlists_data.get('items', [])
    playlist_info = [(pl['name'], pl['external_urls']['spotify']) for pl in playlists]

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
    <h3>Create a New Playlist</h3>
    <form action="{{ url_for('create_playlist') }}" method="post">
        <input type="text" name="playlist_name" placeholder="Playlist Name" required>
        <input type="text" name="playlist_description" placeholder="Description">
        <button type="submit">Create</button>
    </form>
    <hr>
    <h3>Create Smart Playlist (Recent Listens)</h3>
    <form action="{{ url_for('create_smart_playlist') }}" method="post">
        <input type="text" name="playlist_name" placeholder="Playlist Name">
        <button type="submit">Create Smart Playlist</button>
    </form>
    <hr>
    <a href="{{ url_for('now_playing') }}">🎵 Now Playing (with controls)</a><br>
    <a href="{{ url_for('top_genres') }}">🎧 Your Top Genres</a><br>
    <a href="{{ url_for('top_artists') }}">🎤 Your Top Artists</a><br>
    <a href="{{ url_for('top_tracks') }}">🎼 Your Top Tracks</a><br>
    <a href="{{ url_for('logout') }}">Logout</a>
    """
    return render_template_string(html, playlists=playlist_info)


@app.route("/create_playlist", methods=["POST"])
def create_playlist():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())

    playlist_name = request.form['playlist_name']
    playlist_description = request.form['playlist_description']
    user_id = sp.current_user()['id']

    new_playlist = sp.user_playlist_create(
        user=user_id,
        name=playlist_name,
        public=False,
        description=playlist_description
    )

    return f"✅ Playlist '{playlist_name}' created!<br>" \
           f"<a href='{new_playlist['external_urls']['spotify']}' target='_blank'>Open it on Spotify</a><br>" \
           f"<a href='/get_playlists'>Go back</a>"


@app.route("/create_smart_playlist", methods=["POST"])
def create_smart_playlist():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())

    user_id = sp.current_user()['id']
    playlist_name = request.form.get("playlist_name") or "Smart Playlist"
    playlist_description = "Songs from your most recent listens!"

    new_playlist = sp.user_playlist_create(
        user=user_id,
        name=playlist_name,
        public=False,
        description=playlist_description
    )

    track_uris = set()

    # Add top tracks
    top_tracks_data = sp.current_user_top_tracks(limit=20, time_range='short_term')
    for track in top_tracks_data['items']:
        track_uris.add(track['uri'])

    # Add top artist tracks
    top_artists_data = sp.current_user_top_artists(limit=10, time_range='short_term')
    for artist in top_artists_data['items']:
        top_tracks_artist = sp.artist_top_tracks(artist['id'])
        for track in top_tracks_artist['tracks'][:2]:
            track_uris.add(track['uri'])

    # Add genre-based recommendations
    genres = []
    for artist in top_artists_data['items']:
        genres.extend(artist['genres'])
    genre_counts = Counter(genres)
    top_genres = [genre for genre, _ in genre_counts.most_common(3)]

    for genre in top_genres:
        results = sp.search(q=f"genre:\"{genre}\"", type='artist', limit=5)
        for artist in results['artists']['items']:
            top_tracks_artist = sp.artist_top_tracks(artist['id'])
            if top_tracks_artist['tracks']:
                track_uris.add(top_tracks_artist['tracks'][0]['uri'])

    if track_uris:
        sp.playlist_add_items(new_playlist['id'], list(track_uris))

    return f"✅ Smart Playlist '{playlist_name}' created!<br>" \
           f"<a href='{new_playlist['external_urls']['spotify']}' target='_blank'>Open it on Spotify</a><br>" \
           f"<a href='/get_playlists'>Go back</a>"


@app.route("/now_playing", methods=["GET", "POST"])
def now_playing():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())

    if request.method == "POST":
        action = request.form.get("action")
        if action == "pause":
            sp.pause_playback()
        elif action == "play":
            sp.start_playback()
        elif action == "next":
            sp.next_track()
        elif action == "previous":
            sp.previous_track()

    playback = sp.current_playback()
    if not playback or not playback.get('item'):
        return "❌ No song is currently playing.<br><a href='/get_playlists'>Go back</a>"

    track = playback['item']
    track_name = track['name']
    artists = ", ".join([artist['name'] for artist in track['artists']])
    album_name = track['album']['name']
    track_url = track['external_urls']['spotify']
    album_image = track['album']['images'][0]['url'] if track['album']['images'] else ""
    is_playing = playback['is_playing']

    html = f"""
    <h2>Now Playing</h2>
    <p><strong>Track:</strong> {track_name}</p>
    <p><strong>Artist(s):</strong> {artists}</p>
    <p><strong>Album:</strong> {album_name}</p>
    <a href="{track_url}" target="_blank">Open on Spotify</a><br>
    <img src="{album_image}" alt="Album cover" width="300"><br>
    <form method="post">
        <button type="submit" name="action" value="previous">⏮ Previous</button>
        <button type="submit" name="action" value="{'pause' if is_playing else 'play'}">
            {'⏸ Pause' if is_playing else '▶ Play'}
        </button>
        <button type="submit" name="action" value="next">⏭ Next</button>
    </form>
    <br><a href='/get_playlists'>Go back</a>
    """
    return html


@app.route("/top_genres")
def top_genres():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())

    top_artists_data = sp.current_user_top_artists(limit=20, time_range='short_term')
    genres = []
    for artist in top_artists_data['items']:
        genres.extend(artist['genres'])
    if not genres:
        return "No genres found in your recent listens.<br><a href='/get_playlists'>Go back</a>"

    genre_counts = Counter(genres)
    html = "<h2>🎶 Your Most Listened Genres (Last 4 Weeks)</h2><ul>"
    for genre, count in genre_counts.most_common(10):
        html += f"<li>{genre} ({count} artists)</li>"
    html += "</ul><a href='/get_playlists'>Go back</a>"

    return html


@app.route("/top_artists")
def top_artists():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())

    top_artists_data = sp.current_user_top_artists(limit=10, time_range='short_term')
    if not top_artists_data['items']:
        return "No top artists found in your recent listens.<br><a href='/get_playlists'>Go back</a>"

    html = "<h2>🎤 Your Top Artists (Last 4 Weeks)</h2><ul>"
    for artist in top_artists_data['items']:
        html += f"<li>{artist['name']} ({artist['followers']['total']} followers)</li>"
    html += "</ul><a href='/get_playlists'>Go back</a>"
    return html


@app.route("/top_tracks")
def top_tracks():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())

    top_tracks_data = sp.current_user_top_tracks(limit=10, time_range='short_term')
    if not top_tracks_data['items']:
        return "No top tracks found in your recent listens.<br><a href='/get_playlists'>Go back</a>"

    html = "<h2>🎵 Your Top Tracks (Last 4 Weeks)</h2><ul>"
    for track in top_tracks_data['items']:
        artists = ", ".join([a['name'] for a in track['artists']])
        html += f"<li>{track['name']} — {artists}</li>"
    html += "</ul><a href='/get_playlists'>Go back</a>"
    return html


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


if __name__ == '__main__':
    app.run(debug=True)
