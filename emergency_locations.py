import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), 'gathering_areas', 'iller')

def load_city_data(city_name):
    path = os.path.join(DATA_DIR, f"{city_name}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def get_all_emergency_points(city_name):
    data = load_city_data(city_name)
    if not data:
        return []
    city_info = list(data.values())[0]
    points = []
    for ilce_key, ilce_val in city_info.get("ilceler", {}).items():
        for mahalle_key, mahalle_val in ilce_val.get("mahalleler", {}).items():
            for alan_id, alan_info in mahalle_val.get("toplanmaAlanlari", {}).items():
                points.append({
                    "id": alan_id,
                    "name": alan_info.get("tesis_adi"),
                    "address": alan_info.get("acik_adres"),
                    "lat": alan_info.get("x"),
                    "lon": alan_info.get("y"),
                    "district": alan_info.get("ilce_adi"),
                    "neighborhood": alan_info.get("mahalle_adi"),
                })
    return points
