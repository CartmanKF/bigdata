from flask import Flask, render_template, request, jsonify
from pymongo import MongoClient
import folium
from datetime import datetime, timedelta
from geopy.distance import geodesic
import threading
import time
import requests
import pytz

app = Flask(__name__)

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
            one_hour_ago_tr = now_tr - timedelta(hours=1)
            now_utc = now_tr.astimezone(pytz.utc)
            one_hour_ago_utc = one_hour_ago_tr.astimezone(pytz.utc)
            start = one_hour_ago_utc.strftime('%Y-%m-%dT%H:%M:%S')
            end = now_utc.strftime('%Y-%m-%dT%H:%M:%S')
            url = f"https://servisnet.afad.gov.tr/apigateway/deprem/apiv2/event/filter?start={start}&end={end}"
            print(f"[FETCH] Son 1 saatlik: {url}")

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

@app.route('/')
def index():
    # Türkiye'nin merkezi için otomatik harita
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
            date_utc = quake['Date']
            # Her durumda timezone-aware yap
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

        # Türkiye saatine göre tarih aralığı al
        start_dt_tr = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt_tr = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
        turkey = TURKEY
        # UTC'ye çevir
        start_dt_tr = turkey.localize(start_dt_tr)
        end_dt_tr = turkey.localize(end_dt_tr)
        start_dt_utc = start_dt_tr.astimezone(pytz.utc)
        end_dt_utc = end_dt_tr.astimezone(pytz.utc)

        # UTC'ye göre veritabanı sorgusu
        results = list(collection.find({
            "Date": {"$gte": start_dt_utc, "$lte": end_dt_utc}
        }, {"_id": 0}))
        results += list(rt_collection.find({
            "Date": {"$gte": start_dt_utc, "$lte": end_dt_utc}
        }, {"_id": 0}))

        # Son 1 saat için UTC'ye göre aralık
        now_tr = datetime.now(turkey)
        one_hour_ago_tr = now_tr - timedelta(hours=1)
        now_utc = now_tr.astimezone(pytz.utc)
        one_hour_ago_utc = one_hour_ago_tr.astimezone(pytz.utc)

        m = folium.Map(location=[lat, lon], zoom_start=7)
        for quake in results:
            try:
                qlat = float(str(quake["Latitude"]).replace(",", "."))
                qlon = float(str(quake["Longitude"]).replace(",", "."))
                date_utc = quake['Date']
                # Her durumda timezone-aware yap
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
                date_tr = date_utc.astimezone(TURKEY)
                tarih_str = date_tr.strftime("%Y-%m-%d %H:%M:%S")
                color = "blue"
                if one_hour_ago_utc <= date_utc <= now_utc:
                    color = "red"
                distance = geodesic((lat, lon), (qlat, qlon)).km
                if distance <= radius:
                    color = "purple"
                popup = f"{tarih_str}<br>{quake['Location']}<br>M {quake['Magnitude']}"
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
        date_utc = last_eq["Date"]
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
        date_tr = date_utc.astimezone(TURKEY)
        last_eq["Date"] = date_tr.strftime("%Y-%m-%d %H:%M:%S")
        last_eq["_id"] = str(last_eq["_id"])
        return jsonify(last_eq)
    return jsonify({})

@app.route('/last50')
def last50():
    # Son 50 depremi hem realtime'dan hem historical'dan çek, tarihe göre sırala
    all_eq = list(collection.find({}, {"_id": 0})) + list(rt_collection.find({}, {"_id": 0}))
    all_eq.sort(key=lambda x: x.get("Date"), reverse=True)
    last50 = all_eq[:50]
    return render_template("last50.html", earthquakes=last50)

if __name__ == "__main__":
    fetch_thread = threading.Thread(target=fetch_afad_loop, daemon=True)
    fetch_thread.start()
    print("✅ Flask başlatıldı: http://127.0.0.1:5000")
    app.run(debug=True)