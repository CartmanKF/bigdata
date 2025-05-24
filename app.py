from flask import Flask, render_template, request, jsonify, session
from pymongo import MongoClient
import folium
from datetime import datetime, timedelta
from geopy.distance import geodesic
from folium.features import DivIcon
import threading
import time
import requests
import pytz

app = Flask(__name__)
app.secret_key = "super-secret-key"  # Session için gerekli

client = MongoClient("mongodb+srv://cartmankf:H3Ppd2xIyGDMAVoO@cluster0.gyh12d4.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
db = client["earthquake_db"]
collection = db["historical_earthquakes"]
rt_collection = db["realtime_earthquakes"]

TURKEY = pytz.timezone("Europe/Istanbul")
fetch_lock = threading.Lock()

def fetch_afad_loop():
    while True:
        with fetch_lock:
            now_tr = datetime.now(TURKEY)
            two_hours_ago_tr = now_tr - timedelta(hours=2)
            now_utc = now_tr.astimezone(pytz.utc)
            two_hours_ago_utc = two_hours_ago_tr.astimezone(pytz.utc)
            start = two_hours_utc.strftime('%Y-%m-%dT%H:%M:%S')
            end = now_utc.strftime('%Y-%m-%dT%H:%M:%S')
            url = f"https://servisnet.afad.gov.tr/apigateway/deprem/apiv2/event/filter?start={start}&end={end}"
            print(f"[FETCH] Son 2 saatlik: {url}")

            try:
                response = requests.get(url, timeout=15)
                data = response.json()
            except Exception as e:
                print("[AFAD] Veri alınamadı:", e)
                time.sleep(10)
                continue

            if isinstance(data, list):
                eq_list = data
            elif isinstance(data, dict) and "result" in data:
                eq_list = data["result"]
            else:
                print("Beklenmeyen veri formatı!")
                time.sleep(10)
                continue

            print(f"[INFO] Gelen deprem kaydı sayısı: {len(eq_list)}")
            eklenen = 0
            for eq in eq_list:
                event_id = eq.get("eventID")
                location = eq.get("location")
                if not event_id:
                    print("eventID yok, kayıt atlandı:", location)
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
                        print(f"[EKLENDİ] {event_id} - {location} - {date_utc}")
                        eklenen += 1
                    except Exception as e:
                        print(f"[HATA] {event_id} {location}: {e}")
                        continue
                else:
                    print(f"[ATLADI] {event_id} - {location}")
            if eklenen:
                print(f"✅ {eklenen} yeni deprem eklendi [{datetime.now(TURKEY)}]")
        time.sleep(10)

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

def parse_utc_date(date_utc):
    if isinstance(date_utc, str):
        try:
            date_utc = datetime.strptime(date_utc, "%Y-%m-%d %H:%M:%S")
            date_utc = pytz.utc.localize(date_utc)
        except:
            date_utc = datetime.fromisoformat(date_utc)
            if date_utc.tzinfo is None:
                date_utc = pytz.utc.localize(date_utc)
    elif date_utc.tzinfo is None:
        date_utc = pytz.utc.localize(date_utc)
    return date_utc

@app.route('/')
def index():
    turkey_center = (39.0, 35.0)
    now_utc = datetime.now(pytz.utc)
    one_week_ago_utc = now_utc - timedelta(days=7)
    results = list(collection.find({"Date": {"$gte": one_week_ago_utc}}, {"_id": 0}))
    results += list(rt_collection.find({"Date": {"$gte": one_week_ago_utc}}, {"_id": 0}))

    m = folium.Map(location=turkey_center, zoom_start=6)
    turkey = TURKEY
    for quake in results:
        try:
            qlat = float(str(quake["Latitude"]).replace(",", "."))
            qlon = float(str(quake["Longitude"]).replace(",", "."))
            date_utc = parse_utc_date(quake['Date'])

            date_tr = date_utc.astimezone(turkey)
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
            print("Harita hatası:", e)
            continue

    m.save("templates/map.html")
    return render_template("index.html")

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
                date_utc = parse_utc_date(quake['Date'])

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
                print("Harita hatası:", e)
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
        date_utc = parse_utc_date(last_eq["Date"])
        date_tr = date_utc.astimezone(TURKEY)
        last_eq["Date"] = date_tr.strftime("%Y-%m-%d %H:%M:%S")
        last_eq["_id"] = str(last_eq["_id"])
        return jsonify(last_eq)
    return jsonify({})

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
            lat, lon = 41.0082, 28.9784  # İstanbul koordinatları
        else:
            lat, lon = get_location_from_ip(ip)
            if lat is None or lon is None:
                lat, lon = 39.0, 35.0  # Türkiye ortalaması fallback

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
        if lat is not None and lon is not None:
            dist = geodesic((lat, lon), (eq_lat, eq_lon)).km
            eq['near_user'] = dist <= max_distance_km
            if eq['near_user']:
                near_eq_count += 1
        else:
            eq['near_user'] = False

    return render_template("last50.html", earthquakes=last50, near_eq_count=near_eq_count, user_location=(lat, lon))

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

if __name__ == "__main__":
    fetch_thread = threading.Thread(target=fetch_afad_loop, daemon=True)
    fetch_thread.start()
    print("✅ Flask başlatıldı: http://127.0.0.1:5000")
    app.run(debug=True)
