import os
from flask import Flask, session, redirect, url_for, request, render_template_string
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler
from collections import Counter

# Spotify credentials
client_id = "edb8e43341cd46eb8c240d3bfd01e590"
client_secret = "49dba5129cdd414187ac758a53c2b7f4"
redirect_uri = "http://127.0.0.1:5000/callback"

# Required scopes
scope = ("playlist-read-private playlist-modify-private playlist-modify-public "
         "user-read-playback-state user-read-currently-playing user-top-read")

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(64)

# Spotify OAuth handler
cache_handler = FlaskSessionCacheHandler(session)
sp_oauth = SpotifyOAuth(
    client_id=client_id,
    client_secret=client_secret,
    redirect_uri=redirect_uri,
    scope=scope,
    cache_handler=cache_handler,
    show_dialog=True
)


def get_spotify_client():
    token_info = cache_handler.get_cached_token()
    if token_info is None or not sp_oauth.validate_token(token_info):
        return None


    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        cache_handler.save_token_to_cache(token_info)

    return Spotify(auth=token_info['access_token'])


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
<a href="{{ url_for('top_artists') }}">🎤 See Your Top Artists</a><br>
<a href="{{ url_for('top_tracks') }}">🎵 See Your Top Tracks</a><br>
<a href="{{ url_for('top_genres') }}">🎧 See Your Top Genres</a><br>
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

@app.route("/now_playing")
def now_playing():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())

    playback = sp.current_playback()
    if not playback or not playback.get('item'):
        return "❌ No song is currently playing.<br><a href='/get_playlists'>Go back</a>"

    track = playback['item']
    track_name = track['name']
    artists = ", ".join([artist['name'] for artist in track['artists']])
    album_name = track['album']['name']
    track_url = track['external_urls']['spotify']
    album_image = track['album']['images'][0]['url'] if track['album']['images'] else ""

    html = f"""
    <h2>Now Playing</h2>
    <p><strong>Track:</strong> {track_name}</p>
    <p><strong>Artist(s):</strong> {artists}</p>
    <p><strong>Album:</strong> {album_name}</p>
    <a href="{track_url}" target="_blank">Open on Spotify</a><br>
    <img src="{album_image}" alt="Album cover" width="300"><br>
    <a href='/get_playlists'>Go back</a>
    """
    return html

@app.route("/top_genres")
def top_genres():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())

 
    top_artists = sp.current_user_top_artists(limit=20, time_range ='medium_term')
        

    if not top_artists['items']:
        return "No top artists found. Make sure you have enough listening history.<br><a href='/get_playlists'>Go back</a>"

    genres = []
    for artist in top_artists['items']:
        genres.extend(artist['genres'])

    if not genres:
        return "No genres found from top artists.<br><a href='/get_playlists'>Go back</a>"

    from collections import Counter
    genre_counts = Counter(genres)

    html = "<h2>🎶 Your Most Listened Genres</h2><ul>"
    for genre, count in genre_counts.most_common(5):
        html += f"<li>{genre} ({count} artists)</li>"
    html += "</ul><a href='/get_playlists'>Go back</a>"

    return html

@app.route("/top_tracks")
def top_tracks():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())


 
    top_tracks_data = sp.current_user_top_tracks(limit=10, time_range='medium_term')


    if not top_tracks_data['items']:
        return "No top tracks found.<br><a href='/get_playlists'>Go back</a>"

    html = "<h2>🎵 Your Top Tracks</h2><ul>"
    for track in top_tracks_data['items']:
        artists = ", ".join([a['name'] for a in track['artists']])
        html += f"<li>{track['name']} — {artists}</li>"
    html += "</ul><a href='/get_playlists'>Go back</a>"

    return html

@app.route("/top_artists")
def top_artists():
    sp = get_spotify_client()
    if sp is None:
        return redirect(sp_oauth.get_authorize_url())


    top_artists_data = sp.current_user_top_artists(limit=10, time_range='medium_term')


    if not top_artists_data['items']:
        return "No top artists found.<br><a href='/get_playlists'>Go back</a>"

    html = "<h2>🎤 Your Top Artists</h2><ul>"
    for artist in top_artists_data['items']:
        html += f"<li>{artist['name']} ({artist['followers']['total']} followers)</li>"
    html += "</ul><a href='/get_playlists'>Go back</a>"

    return html



@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

if __name__ == '__main__':
    app.run(debug=True)

