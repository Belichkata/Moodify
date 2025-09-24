import os
from flask import Flask, session, redirect, url_for, request

import spotipy
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler


client_id="edb8e43341cd46eb8c240d3bfd01e590"          
client_secret="49dba5129cdd414187ac758a53c2b7f4"
redirect_uri="http://127.0.0.1:5000/callback"
scope="playlist-read-private"

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(64)

cache_handler = FlaskSessionCacheHandler(session)
sp_oauth = SpotifyOAuth(
    client_id = client_id,
    client_secret = client_secret,
    redirect_uri = redirect_uri,
    scope = scope,
    cache_handler=cache_handler,
    show_dialog=True
)

sp = Spotify(auth_manager = sp_oauth)

@app.route("/")
def home():
    if not sp_oauth.validate_token(cache_handler.get_cached_token()):
        auth_url = sp_oauth.get_authorize_url()
        return redirect(auth_url)
    return redirect(url_for("get_playlists"))

@app.route("/callback")
def callback():
    sp_oauth.get_access_token(request.args['code'])
    return redirect(url_for('get_playlists'))

@app.route("/get_playlists")
def get_playlists():
    if not sp_oauth.validate_token(cache_handler.get_cached_token()):
        auth_url = sp_oauth.get_authorize_url()
        return redirect(auth_url)
    
    playlists = sp.current_user_playlists()
    playlist_info = [(pl['name'],pl['external_urls']['spotify'])for pl in playlists['items']] 
    playlist_html = '<br>'.join(f'{name}:{url}' for name,url in playlist_info)

    return playlist_html

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

if __name__ =='__main__':
    app.run(debug=True)




