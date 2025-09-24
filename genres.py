
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from collections import Counter


sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id="edb8e43341cd46eb8c240d3bfd01e590",
        
    client_secret="49dba5129cdd414187ac758a53c2b7f4",
    redirect_uri="http://127.0.0.1:8081/callback",
    scope="user-top-read"
))


top_artists = sp.current_user_top_artists(limit=20, time_range='medium_term')

genres = []

for artist in top_artists['items']:
    genres.extend(artist['genres'])  


genre_counts = Counter(genres)

print("🎶 Your Most Listened Genres:")
for genre, count in genre_counts.most_common(5):  
    print(f"{genre} ({count} artists)")