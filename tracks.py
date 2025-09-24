import spotipy
from spotipy.oauth2 import SpotifyOAuth

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id="edb8e43341cd46eb8c240d3bfd01e590",
    client_secret="49dba5129cdd414187ac758a53c2b7f4",
    redirect_uri="http://127.0.0.1:8081/callback",
    scope="user-top-read"
))

top_tracks = sp.current_user_top_tracks(limit=10, time_range='medium_term')

print("🎵 Your Top Tracks:")
for idx, track in enumerate(top_tracks['items']):
    artists = ", ".join([artist['name'] for artist in track['artists']])
    print(f"{idx+1}. {track['name']} — {artists}")
