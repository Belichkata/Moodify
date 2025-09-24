import spotipy
from spotipy.oauth2 import SpotifyOAuth
from openai import OpenAI
import random

# --- Spotify Auth ---
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id="edb8e43341cd46eb8c240d3bfd01e590",
    client_secret="49dba5129cdd414187ac758a53c2b7f4",
    redirect_uri="http://127.0.0.1:5000/callback",
    scope="user-top-read playlist-modify-private playlist-modify-public"
))

# --- OpenAI Client ---
client = OpenAI(api_key="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")

def get_user_top_genres():
    """Fetch user's most listened-to genres based on top artists."""
    top_artists = sp.current_user_top_artists(limit=20)
    genres = []
    for artist in top_artists['items']:
        genres.extend(artist['genres'])
    return list(set(genres))

def get_related_genres(user_genres):
    """
    Generate fallback genres that are *related* to the user's taste
    but not exact duplicates.
    """
    prompt = f"""
    The user's top genres are: {user_genres}.
    Suggest 3 genres that are different but complementary — something they might enjoy based on their taste.
    Return them as a JSON list, e.g. ["genre1","genre2","genre3"].
    """

    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a music taste assistant. Only return a JSON list of genres, no extra words."},
            {"role": "user", "content": prompt}
        ]
    )

    try:
        response = completion.choices[0].message.content.strip()
        related_genres = eval(response)
    except:
        # If GPT fails, just shuffle the existing genres and pick 3 different ones
        related_genres = random.sample(user_genres, min(len(user_genres), 3))
    return related_genres

def ask_ai_for_genres(condition_description):
    """Ask GPT to suggest genres for the given driving condition."""
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
        genres = eval(response)
    except:
        genres = []
    return genres

def create_condition_playlist(condition_description):
    """Generate genres, compare with user preferences, create a playlist."""
    user_genres = get_user_top_genres()
    ai_genres = ask_ai_for_genres(condition_description)

    # Prioritize user's genres if they overlap with AI's suggestion
    prioritized = [g for g in ai_genres if g in user_genres]

    # If we don't have enough genres, fill with AI genres first
    for g in ai_genres:
        if g not in prioritized and len(prioritized) < 3:
            prioritized.append(g)

    # If still not enough, use *related* genres from GPT
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

    print(f"✅ Playlist created for '{condition_description}' with genres: {prioritized[:3]}")

# --- Example Run ---
create_condition_playlist("Nighttime highway driving with light rain and heavy traffic")

