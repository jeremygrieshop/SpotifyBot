#!/usr/bin/python

import time
import pytz
import praw
import spotipy
import spotipy.util as util
from spotipy import oauth2
import pprint
import threading
import ConfigParser
import MySQLdb
import traceback
import datetime
import tzlocal
import lxml
import urllib
import sys
from fuzzywuzzy import fuzz
from fuzzywuzzy import process

from lxml import etree
from praw.errors import ExceptionList, APIException, RateLimitExceeded

# Read the config file
config = ConfigParser.ConfigParser()
config.read("config.txt")

# Set our Reddit Bot Account
reddit_user = config.get("Reddit", "username")
reddit_pw = config.get("Reddit", "password")

# Set our Spotify Account variables
spotipy_username = config.get("Spotify", "spotipy_username")
spotipy_client_id = config.get("Spotify", "spotipy_client_id")
spotipy_client_secret = config.get("Spotify", "spotipy_client_secret")
spotipy_redirect_uri = config.get("Spotify", "spotipy_redirect_uri")

# Connect to our database
db_user = config.get("SQL", "username")
db_pw = config.get("SQL", "password")
db_database = config.get("SQL", "database")

db = MySQLdb.connect(host="localhost", user=db_user, passwd=db_pw, db=db_database)

# Subreddits to look for
subreddits = "SpotifyBot+IndieHeads+AskReddit+Music+listentothis"
#subreddits = "SpotifyBot"

# define a few template messages
msg_created = (
	"Greetings from the SpotifyBot!"
	"\n\nBased on your comments, "
	"I think you requested a Spotify Playlist to be created."
	"This playlist has been auto-generated for you:"
	"\n\n{playlist}"
	)

msg_pm_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments in this submission:\n\n {submission}"
	"\n\nThis playlist has been auto-generated for you:"
	"\n\n{playlist}"
	)

msg_already_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments.  A playlist has already been created here:"
	"\n\n{playlist}"
	)

msg_pm_already_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments in this submission:\n\n {submission}"
	"\n\nA playlist has already been created here:"
	"\n\n{playlist}"
	)

msg_pm_no_tracks = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments in this submission:\n\n {submission}"
	"\n\nUnfortunately, I could not find any valid tracks from the top-level comments!"
	)
	
msg_no_tracks = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments.  "
	"Unfortunately, I could not find any valid tracks from the top-level comments!"
	)


def log(message):
	print "[" + time.strftime("%c") + "] " + message

def append_submission_to_db(submission, playlist):
	db_cursor = db.cursor()
	cmd = "insert into Submissions (submission_url, playlist_url) values(%s, %s)"
	db_cursor.execute(cmd, [submission.url, playlist])
	db.commit()
	db_cursor.close()

def get_submission_playlist(submission_url):
	db_cursor = db.cursor()
	query = "select submission_url, playlist_url from Submissions where submission_url=%s"
	db_cursor.execute(query, [submission_url])
	data = db_cursor.fetchall()
	db_cursor.close()

	if len(data) == 0:
		return None
	else:
		for row in data:
			# should only be one row returned
			return row[1]
		
def append_comment_to_db(comment_id):
	db_cursor = db.cursor()
	cmd = "insert into Comments (comment_id) values(%s)"
	db_cursor.execute(cmd, [comment_id])
	db.commit()
	db_cursor.close()

def has_commented(comment_id):
	db_cursor = db.cursor()
	query = "select comment_id from Comments where comment_id = %s"
	db_cursor.execute(query, [comment_id])
	data = db_cursor.fetchall()
	db_cursor.close()

	if len(data) == 0:
		return False
	else:
		return True

def parse_youtube_link(spotify, link):
	url = urllib.urlopen(link)
	if url:
		youtube = etree.HTML(url.read())
		title = youtube.xpath("//span[@id='eow-title']/@title")
		if title:
			track = parse_track(spotify, ''.join(title))
			return track

def parse_track(spotify, line):

	# see if we have a youtube link
	if ("www.youtube.com/" in line):
		for word in line.split():
			if ("www.youtube.com/" in word):
				if "(" in word:
					t = parse_youtube_link(
						spotify, 
						word.split("(",1)[1].split(")",1)[0])
					return t
				else:
					t = parse_youtube_link(spotify, word)
					return t

	search_text = line
	if line.count(" by ") == 1:
		search_text = line.replace(" by ", " ")
	if line.count("-") == 1:
		search_text = line.replace("-", " ")
	search_text = search_text + " AND NOT Karaoke"

	results = spotify.search(search_text, limit=50, type='track')

	items = results['tracks']['items']

	choices = []
	track_hash = {}

	if len(items) > 0:
		for t in items:
			choices.append(t['artists'][0]['name'] + " " + t['name'])
			track_hash[t['artists'][0]['name'] + " " + t['name']] = t
			choices.append(t['name'] + " " + t['artists'][0]['name'])
			track_hash[t['name'] + " " + t['artists'][0]['name']] = t

		best_track = process.extractOne(search_text, choices)
		best_t = track_hash[best_track[0]]

		return best_t

	return None

def find_tracks(spotify, submission):
	tracks = {}

	for track_comment in submission.comments:
		if isinstance(track_comment, praw.objects.MoreComments):
			continue

		for line in track_comment.body.split('\n'):
			if (not line):
				continue
			track = parse_track(spotify, line)
			if track:
				if not track['uri'] in tracks:
					tracks[track['uri']] = track
					if track_comment.author:
						log("Found track " + 
							track['uri'] + 
							" for author " + 
							track_comment.author.name)

	return tracks

def populate_playlist(spotify, playlist, tracks):

	try:
		spotify.user_playlist_add_tracks(spotipy_username, playlist['id'], tracks)
	except Exception as err:
			log("Error adding track")
			log(err)

def create_playlist(spotify, title):

	playlist = spotify.user_playlist_create(spotipy_username, title)
	if playlist:
		return playlist

	return None

def comment_wants_playlist(body):
	if len(body.split()) > 25:
		# Skipping wall of text
		return False

	# the magic keyword SpotifyBot! always gets a request
	if "spotifybot!" in body.lower():
		return True

	# otherwise, use fuzzy matching
	if fuzz.ratio("Can someone make a Spotify playlist?", body) > 65:
		return True

	return False

def should_private_reply(submission, comment):
	# the jerks over in r/Music don't like bots to post
	if submission.subreddit.display_name.lower() == "music":
		return True
	
	return False

def update_existing_playlist(spotify, list_url, comment):
	if len(comment.body.split('\n')) > 3:
		# Skipping wall of text
		return False

	playlist = spotify.user_playlist(spotipy_username, list_url)
	if not playlist:
		log("Could no longer find playlist")
		return False

	tracks = playlist['tracks']['items']

	for line in comment.body.split('\n'):
		if not line:
			continue

		track = parse_track(spotify, line)
		if track:
			log("Updating existing playlist " + list_url)
			found = False
			if len(tracks) > 0:
				for t in tracks:
					if track['uri'] == t['track']['uri']:
						found = True
						break
			if found == False:
				if comment.author:
					log("Found new track, adding " + 
						track['uri'] + 
						" for author " + 
						comment.author.name)

				spotify.user_playlist_add_tracks(
					spotipy_username, 
					playlist['id'], 
					{track['uri']:track})
			else:
				log("Track already in playlist, skipping")

def create_new_playlist(reddit, spotify, submission, comment):

	tracks = find_tracks(spotify, submission)
	num_tracks = len(tracks)
	log("Found " + str(num_tracks) + " tracks for new playlist")

	if num_tracks > 0:
		# add a new playlist
		new_playlist = create_playlist(spotify, submission.title)
		if new_playlist:
			playlist_url = new_playlist['external_urls']['spotify']
			playlist_name = new_playlist['name']

			log("New playlist: " + playlist_url + " (" + playlist_name + ")")

			populate_playlist(spotify, new_playlist, tracks)

			try:
				if should_private_reply(submission, comment):
					reddit.send_message(
						comment.author.name, 
						"Spotify Playlist", 
						msg_pm_created.format(
							submission=submission.url, 
							playlist=playlist_url))
				else:
					comment.reply(msg_created.format(playlist=playlist_url))
			except Exception as err:
				log("Unable to reply to reddit message: " + str(err))

		append_comment_to_db(comment.id)
		append_submission_to_db(submission, new_playlist['external_urls']['spotify'])
		log("comment and submission recorded in journal")
	else:
		try:
			if should_private_reply(submission, comment):
				reddit.send_message(
					comment.author.name, 
					"Spotify Playlist", 
					msg_pm_no_tracks.format(submission=submission.url))
			else:
				comment.reply(msg_no_tracks)
		except Exception as err:
			log("Unable to reply to reddit messaeg: " + str(err))

		append_comment_to_db(comment.id)
		log("comment recorded in journal")

def process_comment(reddit, spotify, comment):

	# calculate how far back in the queue we currently are
	timestamp = datetime.datetime.utcfromtimestamp(comment.created_utc)
	timestamp_now = datetime.datetime.utcnow()
	diff = (timestamp_now - timestamp)

	log("Processing comment id=" + comment.id + ", user=" + comment.author.name + ", time_ago=" + str(diff))

	# fetch the submission/playlist and check if it's in our database already
	playlist_url = get_submission_playlist(comment.link_url)
	if playlist_url:
		# it's in our database, so see if this is another request, or another track
		log("Submission already recorded, checking comments")
		if comment_wants_playlist(comment.body):
			log("Sending existing playlist: " + playlist_url + " to " + comment.author.name)
			submission = reddit.get_submission(comment.link_url)
			if should_private_reply(submission, comment):
				reddit.send_message(
					comment.author.name, 
					"Spotify Playlist", 
					msg_pm_already_created.format(
						submission=submission.url, 
						playlist=playlist_url))
			else:
				comment.reply(msg_already_created.format(playlist=playlist_url))

			append_comment_to_db(comment.id)
		elif comment.is_root:
			# already processed this submission, but perhaps this is a new track to add
			log("\n------- Update Playlist ----------------")

			try:
				update_existing_playlist(spotify, playlist_url, comment)
				append_comment_to_db(comment.id)
			except Exception as err:
				log("Error updating playlist")
				log(err)
				print traceback.format_exc()
		else:
			log("Not a request for playlist, but also not a top-level comment")

	else:
		# it's not in our database, so see if they are requesting a playlist
		if comment_wants_playlist(comment.body):
			submission = reddit.get_submission(comment.link_url)

			log("\n------- Create Playlist ------------------")
			try:
				create_new_playlist(reddit, spotify, submission, comment)
			except Exception as err:
				log("Error creating new playlist")
				log(err)

def reddit_login():

	reddit = praw.Reddit('Spotify Playlist B0t v1.0')
	reddit.login(reddit_user, reddit_pw, disable_warning=True)

	return reddit

def spotify_login():

	token = util.prompt_for_user_token(
			spotipy_username,
			client_id=spotipy_client_id,
			client_secret=spotipy_client_secret,
			redirect_uri=spotipy_redirect_uri)
	if not token:
		log("Unable to login to spotify")
		sys.exit()

	spotify = spotipy.Spotify(auth=token)

	return spotify	

def database_login():

	db = MySQLdb.connect(host="localhost", user=db_user, passwd=db_pw, db="spotifybot")

	return db

def test_search(search_text):

	spotify = spotify_login()

	track = parse_track(spotify, search_text)

	print("Best fit: " + track['name'] + " by " + track['artists'][0]['name'])

def test_submission(link_url):

	spotify = spotify_login()
	reddit = reddit_login()

	submission = reddit.get_submission(link_url)

	tracks = find_tracks(spotify, submission)

	print("Listing tracks..")
	for t in tracks:
		track = tracks[t]
		print(track['name'] + " by " + track['artists'][0]['name'])

def test_create_playlist(title, link_url):

	spotify = spotify_login()
	reddit = reddit_login()

	submission = reddit.get_submission(link_url)

	tracks = find_tracks(spotify, submission)

	new_playlist = create_playlist(spotify, title)
	if new_playlist:
		populate_playlist(spotify, new_playlist, tracks)

		print("New playlist created: " + new_playlist['external_urls']['spotify'])

def main():

	# login to reddit for PRAW API
	reddit = reddit_login()

	# login to spotify, using their OAUTH2 API
	spotify = spotify_login()

	while True:
		log("Looking for comments...")
		try:
			for comment in praw.helpers.comment_stream(reddit, subreddits, limit=500, verbosity=0):
				# skip comments we have already processed in our database
				if has_commented(comment.id):
					log("Already processed this comment, ignoring..")
					continue

				# make sure user hasn't been deleted
				if not comment.author:
					continue

				# make sure this comment isn't us!
				if comment.author.name == reddit_user:
					continue

				try:
					# go ahead and attempt to process this comment
					process_comment(reddit, spotify, comment)

				except (RateLimitExceeded, APIException) as e:
					log(e)
					time.sleep(5)

				except Exception as e2:
					log(e2)
					print traceback.format_exc()

		except APIException as e:
			log(e)
			time.sleep(1)

		except Exception as err2:
			print traceback.format_exc()
			time.sleep(5)

if __name__ == '__main__':

	if len(sys.argv) > 1:
		if sys.argv[1] == "search":
			test_search(sys.argv[2])

		elif sys.argv[1] == "submission":
			test_submission(sys.argv[2])

		elif sys.argv[1] == "create_playlist":
			test_create_playlist(sys.argv[2], sys.argv[3])
	else:
		main()
