import requests
import base64
import time
from pymongo import MongoClient

API_KEY = "nsdSkWHGEceBeAfJKa7XXSiGP"
API_SECRET = "B2xAMdxVEMTeTrwSdI4JwgMavXvx1TK0QRoTKWP77o6atrVvBa"

# MongoDB client ve collection global olarak açılıyor
client = MongoClient("mongodb+srv://cartmankf:H3Ppd2xIyGDMAVoO@cluster0.gyh12d4.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
db = client["earthquake_db"]
tweets_collection = db["tweets"]

def get_bearer_token(api_key=API_KEY, api_secret=API_SECRET):
    key_secret = f"{api_key}:{api_secret}".encode('ascii')
    b64_encoded_key = base64.b64encode(key_secret).decode('ascii')

    url = "https://api.twitter.com/oauth2/token"
    headers = {
        "Authorization": f"Basic {b64_encoded_key}",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
    }
    response = requests.post(url, headers=headers, data="grant_type=client_credentials")
    response.raise_for_status()
    token = response.json().get("access_token")
    return token

def search_tweets(bearer_token, hashtag, max_results=20, retries=3, delay=5):
    url = "https://api.twitter.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {bearer_token}"}
    query = f"#{hashtag} -is:retweet lang:tr"
    params = {
        "query": query,
        "max_results": max_results,
        "tweet.fields": "created_at,text,id"
    }

    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()

            remaining = response.headers.get("x-rate-limit-remaining")
            reset_time = response.headers.get("x-rate-limit-reset")
            if remaining is not None and int(remaining) == 0:
                wait_seconds = int(reset_time) - int(time.time())
                print(f"Rate limit aşıldı, {wait_seconds} saniye bekleniyor...")
                time.sleep(max(wait_seconds, 0) + 1)

            data = response.json()
            return data.get("data", [])

        except requests.exceptions.RequestException as e:
            print(f"API isteği hatası: {e}, {attempt+1}. deneme...")
            time.sleep(delay)

    raise Exception("API isteği başarısız oldu, lütfen daha sonra tekrar deneyin.")

def save_tweets_to_db(tweets, collection=tweets_collection):
    for tweet in tweets:
        if not collection.find_one({"id": tweet["id"]}):
            collection.insert_one(tweet)
