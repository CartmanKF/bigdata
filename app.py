import threading
import time
from datetime import datetime, timedelta
import os
import folium
import pytz
import requests
from flask import Flask, render_template, request, jsonify, session
from folium.features import DivIcon
from geopy.distance import geodesic
from pymongo import MongoClient
from gathering_areas.collect import GatheringAreaCollector
import json
from emergency_locations import get_all_emergency_points
from gathering_areas.my_scraper import AFADScraper




import twitter_api  # Twitter API işlemleri için

app = Flask(__name__)
app.secret_key = "super-secret-key"

client = MongoClient("mongodb+srv://cartmankf:H3Ppd2xIyGDMAVoO@cluster0.gyh12d4.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
db = client["earthquake_db"]
collection = db["historical_earthquakes"]
rt_collection = db["realtime_earthquakes"]
tweets_collection = db["tweets"]
gathering_collection = db["gathering_areas"]

TURKEY = pytz.timezone("Europe/Istanbul")
fetch_lock = threading.Lock()

last_twitter_api_call = 0
TWITTER_API_MIN_INTERVAL = 900  # 15 dakika

def parse_date_to_utc(date_obj):
    if isinstance(date_obj, str):
        try:
            date_obj = datetime.strptime(date_obj, "%Y-%m-%d %H:%M:%S")
            date_obj = pytz.utc.localize(date_obj)
        except Exception:
            date_obj = datetime.fromisoformat(date_obj)
            if date_obj.tzinfo is None:
                date_obj = pytz.utc.localize(date_obj)
    elif date_obj.tzinfo is None:
        date_obj = pytz.utc.localize(date_obj)
    return date_obj



    
def save_gathering_areas_to_mongo():
    # Hem kökte hem gathering_areas/iller klasöründe ara
    possible_folders = ["iller", "gathering_areas/iller"]
    found = False
    for folder in possible_folders:
        if os.path.exists(folder):
            found = True
            for filename in os.listdir(folder):
                if filename.endswith(".json"):
                    filepath = os.path.join(folder, filename)
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        city = list(data.keys())[0]  # Ör: "Ankara"
                        gathering_collection.update_one(
                            {"city": city},
                            {"$set": {"data": data[city]}},
                            upsert=True
                        )
            print(f"Acil toplanma yerleri MongoDB'ye kaydedildi: {folder}")
            break
    if not found:
        print("HATA: 'iller' klasörü bulunamadı!")

@app.route('/save_gathering_areas')
def save_gathering_areas():
    try:
        save_gathering_areas_to_mongo()
        return "Veriler MongoDB'ye kaydedildi."
    except Exception as e:
        return f"Hata: {e}"


def fetch_afad_loop():
    while True:
        with fetch_lock:
            now_tr = datetime.now(TURKEY)
            two_hours_ago_tr = now_tr - timedelta(hours=2)
            now_utc = now_tr.astimezone(pytz.utc)
            two_hours_ago_utc = two_hours_ago_tr.astimezone(pytz.utc)
            start = two_hours_ago_utc.strftime('%Y-%m-%dT%H:%M:%S')
            end = now_utc.strftime('%Y-%m-%dT%H:%M:%S')

            url = f"https://servisnet.afad.gov.tr/apigateway/deprem/apiv2/event/filter?start={start}&end={end}"
            print(f"[AFAD FETCH] {url}")

            try:
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                print(f"[AFAD ERROR] Veri çekilemedi: {e}")
                time.sleep(60)
                continue

            if isinstance(data, list):
                eq_list = data
            elif isinstance(data, dict) and "result" in data:
                eq_list = data["result"]
            else:
                print("[AFAD ERROR] Beklenmeyen veri formatı")
                time.sleep(60)
                continue

            eklenen = 0
            for eq in eq_list:
                event_id = eq.get("eventID")
                location = eq.get("location", "")
                if not event_id:
                    continue
                if not rt_collection.find_one({"EventID": event_id}):
                    try:
                        date_utc = datetime.strptime(eq["date"], "%Y-%m-%dT%H:%M:%S")
                        date_utc = pytz.utc.localize(date_utc)
                        doc = {
                            "Date": date_utc,
                            "Longitude": float(eq["longitude"]),
                            "Latitude": float(eq["latitude"]),
                            "Depth": float(eq["depth"]),
                            "Magnitude": float(eq["magnitude"]),
                            "Location": location,
                            "EventID": event_id,
                            "Provider": "AFAD"
                        }
                        rt_collection.insert_one(doc)
                        eklenen += 1
                    except Exception as e:
                        print(f"[AFAD SAVE ERROR] {event_id}: {e}")
                        continue
            if eklenen:
                print(f"✅ {eklenen} yeni deprem eklendi [{datetime.now(TURKEY)}]")
        time.sleep(60)


def get_location_from_ip(ip):
    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,message,lat,lon"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        if data.get("status") == "success":
            return data.get("lat"), data.get("lon")
        else:
            print(f"IP konum hatası: {data.get('message')}")
    except Exception as e:
        print(f"IP konum alma hatası: {e}")
    return None, None


@app.route('/')
def index():
    turkey_center = (39.0, 35.0)
    now_utc = datetime.now(pytz.utc)
    one_week_ago_utc = now_utc - timedelta(days=7)

    results = list(collection.find({"Date": {"$gte": one_week_ago_utc}}, {"_id": 0}))
    results += list(rt_collection.find({"Date": {"$gte": one_week_ago_utc}}, {"_id": 0}))

    m = folium.Map(location=turkey_center, zoom_start=6)
    for quake in results:
        try:
            qlat = float(str(quake["Latitude"]).replace(",", "."))
            qlon = float(str(quake["Longitude"]).replace(",", "."))
            date_utc = parse_date_to_utc(quake['Date'])
            date_tr = date_utc.astimezone(TURKEY)
            tarih_str = date_tr.strftime("%Y-%m-%d %H:%M:%S")
            popup = f"{tarih_str}<br>{quake['Location']}<br>M {quake['Magnitude']}"
            folium.CircleMarker(
                location=[qlat, qlon],
                radius=5,
                color="blue",
                fill=True,
                fill_color="blue",
                fill_opacity=0.7,
                popup=popup
            ).add_to(m)
        except Exception as e:
            print(f"[MAP ERROR] {e}")
            continue
    m.save("templates/map.html")
    return render_template("index.html")

@app.route('/collect_gathering_areas')
def collect_gathering_areas():
    collector = GatheringAreaCollector(cities_file="iller.json")
    try:
        collector.run()
        return "Acil toplanma yerleri başarıyla toplandı ve 'iller/' klasörüne kaydedildi."
    except Exception as e:
        return f"Hata oluştu: {e}"


@app.route('/interactive_map')
def interactive_map():
    return render_template("interactive_map.html")


@app.route('/filtered_map')
def filtered_map():
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        lat = float(request.args.get("lat").replace(",", "."))
        lon = float(request.args.get("lon").replace(",", "."))
        radius = float(request.args.get("radius", "100"))

        turkey = TURKEY

        start_dt_tr = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt_tr = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)

        start_dt_tr = turkey.localize(start_dt_tr)
        end_dt_tr = turkey.localize(end_dt_tr)

        start_dt_utc = start_dt_tr.astimezone(pytz.utc)
        end_dt_utc = end_dt_tr.astimezone(pytz.utc)

        results = list(collection.find({
            "Date": {"$gte": start_dt_utc, "$lte": end_dt_utc}
        }, {"_id": 0}))
        results += list(rt_collection.find({
            "Date": {"$gte": start_dt_utc, "$lte": end_dt_utc}
        }, {"_id": 0}))

        now_tr = datetime.now(turkey)
        two_hours_ago_tr = now_tr - timedelta(hours=2)
        now_utc = now_tr.astimezone(pytz.utc)
        two_hours_ago_utc = two_hours_ago_tr.astimezone(pytz.utc)

        m = folium.Map(location=[lat, lon], zoom_start=7)

        pulse_css = """
        <style>
        .pulse {
          width: 20px;
          height: 20px;
          background: rgba(255, 0, 0, 0.5);
          border-radius: 50%;
          position: relative;
          animation: pulse-animation 2s infinite;
          border: 2px solid rgba(255, 0, 0, 0.8);
          box-sizing: content-box;
        }
        @keyframes pulse-animation {
          0% {
            transform: scale(0.7);
            opacity: 1;
          }
          70% {
            transform: scale(2.5);
            opacity: 0;
          }
          100% {
            transform: scale(0.7);
            opacity: 0;
          }
        }
        </style>
        """
        m.get_root().header.add_child(folium.Element(pulse_css))

        for quake in results:
            try:
                qlat = float(str(quake["Latitude"]).replace(",", "."))
                qlon = float(str(quake["Longitude"]).replace(",", "."))
                date_utc = parse_date_to_utc(quake['Date'])
                date_tr = date_utc.astimezone(turkey)
                tarih_str = date_tr.strftime("%Y-%m-%d %H:%M:%S")

                distance = geodesic((lat, lon), (qlat, qlon)).km

                color = "blue"
                if two_hours_ago_utc <= date_utc <= now_utc:
                    color = "red"
                elif distance <= radius:
                    color = "purple"

                popup = f"{tarih_str}<br>{quake['Location']}<br>M {quake['Magnitude']}"

                if color == "red":
                    folium.Marker(
                        location=[qlat, qlon],
                        popup=popup,
                        icon=DivIcon(
                            icon_size=(20, 20),
                            icon_anchor=(10, 10),
                            html='<div class="pulse"></div>'
                        )
                    ).add_to(m)
                else:
                    folium.CircleMarker(
                        location=[qlat, qlon],
                        radius=7 if color != "blue" else 5,
                        color=color,
                        fill=True,
                        fill_color=color,
                        fill_opacity=0.8,
                        popup=popup
                    ).add_to(m)
            except Exception as e:
                print(f"[MAP ERROR] {e}")
                continue

        folium.Marker(
            location=[lat, lon],
            popup="Seçilen Lokasyon",
            icon=folium.Icon(color='green')
        ).add_to(m)

        m.save("templates/map.html")
        return render_template("map.html")
    except Exception as e:
        return f"<h3>Hata oluştu: {e}</h3>"


@app.route('/last_realtime_eq')
def last_realtime_eq():
    last_eq = rt_collection.find_one(sort=[("Date", -1)])
    if last_eq:
        date_utc = parse_date_to_utc(last_eq['Date'])
        date_tr = date_utc.astimezone(TURKEY)
        last_eq["Date"] = date_tr.strftime("%Y-%m-%d %H:%M:%S")
        last_eq["_id"] = str(last_eq["_id"])
        return jsonify(last_eq)
    return jsonify({})

@app.route('/reverse_geocode')
def reverse_geocode():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    if not lat or not lon:
        return jsonify({'error': 'lat ve lon parametreleri gerekli'}), 400

    try:
        url = f'https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1'
        resp = requests.get(url, headers={'User-Agent': 'YourAppName'})
        data = resp.json()
        address = data.get('display_name', '')
        return jsonify({'location': address})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/last50')
def last50():
    lat = request.args.get('lat', type=float)
    lon = request.args.get('lon', type=float)

    if lat is None or lon is None:
        if request.environ.get('HTTP_X_FORWARDED_FOR'):
            ip = request.environ['HTTP_X_FORWARDED_FOR'].split(',')[0]
        else:
            ip = request.remote_addr

        if ip in ('127.0.0.1', '::1'):
            lat, lon = 41.0082, 28.9784  # İstanbul koordinatları fallback
        else:
            lat, lon = get_location_from_ip(ip)

    session['user_lat'] = lat
    session['user_lon'] = lon

    max_distance_km = 100

    all_eq = list(collection.find({}, {"_id": 0})) + list(rt_collection.find({}, {"_id": 0}))
    all_eq.sort(key=lambda x: x.get("Date"), reverse=True)
    last50 = all_eq[:50]

    near_eq_count = 0
    for eq in last50:
        eq_lat = float(str(eq.get("Latitude", 0)).replace(",", "."))
        eq_lon = float(str(eq.get("Longitude", 0)).replace(",", "."))
        # Date'i Türkiye saatine çevir
        try:
            date_utc = parse_date_to_utc(eq["Date"])
            date_tr = date_utc.astimezone(TURKEY)
            eq["Date"] = date_tr
        except Exception:
            pass
        if lat is not None and lon is not None:
            dist = geodesic((lat, lon), (eq_lat, eq_lon)).km
            eq['near_user'] = dist <= max_distance_km
            if eq['near_user']:
                near_eq_count += 1
        else:
            eq['near_user'] = False

    return render_template("last50.html", earthquakes=last50, near_eq_count=near_eq_count, user_location=(lat, lon))


@app.route("/tweets")
def tweets():
    global last_twitter_api_call
    hashtag = request.args.get("hashtag")
    if not hashtag:
        return jsonify({"tweets": [], "status": "Hashtag parametresi gerekli"}), 200

    now = time.time()
    tweets = list(tweets_collection.find({}, {"_id": 0}).sort("created_at", -1).limit(10))
    status_message = "Son kaydedilen tweetler gösteriliyor."

    if now - last_twitter_api_call >= TWITTER_API_MIN_INTERVAL:
        try:
            token = twitter_api.get_bearer_token()
            new_tweets = twitter_api.search_tweets(token, hashtag)
            twitter_api.save_tweets_to_db(new_tweets, tweets_collection)
            last_twitter_api_call = now
            tweets = new_tweets
            status_message = f"{len(new_tweets)} yeni tweet çekildi."
        except Exception as e:
            print(f"Tweetler: {e}")
            status_message ="Son kaydedilen tweetler gösteriliyor."

    try:
        return jsonify({
            "tweets": tweets,
            "status": status_message
        })
    except Exception as e:
        print(f"JSON dönüştürme hatası: {e}")
        return jsonify({
            "tweets": [],
            "status": "Tweetler gösterilirken bir hata oluştu."
        })


@app.route("/past_tweets")
def past_tweets():
    tweets = list(tweets_collection.find({}, {"_id": 0}).sort("created_at", -1).limit(50))
    return render_template("past_tweets.html", tweets=tweets)

def get_all_emergency_points_from_mongo():
    all_points = []
    for city_doc in gathering_collection.find({}):
        city_data = city_doc.get('data', {})
        for ilce in city_data.get('ilceler', {}).values():
            for mahalle in ilce.get('mahalleler', {}).values():
                for alan in mahalle.get('toplanmaAlanlari', {}).values():
                    lat = alan.get('lat') or alan.get('latitude') or alan.get('y')
                    lon = alan.get('lon') or alan.get('longitude') or alan.get('x')
                    if lat and lon:
                        all_points.append({
                            'name': alan.get('ad') or alan.get('tesis_adi', ''),
                            'lat': float(lat),
                            'lon': float(lon),
                            'address': alan.get('adres') or alan.get('acik_adres', ''),
                            'city': city_doc['city']
                        })
    return all_points

@app.route('/nearest_assembly_points')
def nearest_assembly_points():
    lat = float(request.args.get('lat'))
    lon = float(request.args.get('lon'))
    all_points = get_all_emergency_points_from_mongo()
    points_with_distance = []
    for p in all_points:
        dist = geodesic((lat, lon), (p['lat'], p['lon'])).km
        points_with_distance.append((dist, p))
    points_with_distance.sort(key=lambda x: x[0])
    nearest = [p for _, p in points_with_distance[:5]]
    return jsonify(nearest)


@app.route('/nearby_notifications')
def nearby_notifications():
    lat = session.get('user_lat')
    lon = session.get('user_lon')
    if lat is None or lon is None:
        return jsonify([])

    now_utc = datetime.now(pytz.utc)
    one_minute_ago = now_utc - timedelta(minutes=1)

    recent_eq = list(rt_collection.find({
        "Date": {"$gte": one_minute_ago}
    }, {"_id": 0}))

    nearby_eq = []
    for eq in recent_eq:
        eq_lat = float(str(eq.get("Latitude", 0)).replace(",", "."))
        eq_lon = float(str(eq.get("Longitude", 0)).replace(",", "."))
        dist = geodesic((lat, lon), (eq_lat, eq_lon)).km
        if dist <= 100:
            nearby_eq.append({
                "location": eq.get("Location"),
                "magnitude": eq.get("Magnitude"),
                "date": eq.get("Date").strftime("%Y-%m-%d %H:%M:%S"),
                "distance_km": round(dist, 2)
            })
    return jsonify(nearby_eq)

@app.route('/fetch_earthquake_tweets')
def fetch_earthquake_tweets():
    try:
        token = twitter_api.get_bearer_token()
        hashtag = 'deprem (türkiye OR ankara OR istanbul OR izmir)'
        tweets = twitter_api.search_tweets(token, hashtag, max_results=50)
        twitter_api.save_tweets_to_db(tweets, tweets_collection)
        return jsonify({'success': True, 'count': len(tweets)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == "__main__":
    # Sadece ana process'te AFAD thread başlat
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        fetch_thread = threading.Thread(target=fetch_afad_loop, daemon=True)
        fetch_thread.start()
    print("✅ Flask başlatıldı: http://127.0.0.1:5000")
    app.run(debug=True)
