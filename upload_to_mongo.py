import pandas as pd
from pymongo import MongoClient

# 1. MongoDB bağlantısı
mongo_uri = "mongodb+srv://cartmankf:H3Ppd2xIyGDMAVoO@cluster0.gyh12d4.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(mongo_uri)

# 2. Veritabanı ve koleksiyon
db = client["earthquake_db"]
collection = db["historical_earthquakes"]

# 3. Excel dosyasını oku
df = pd.read_excel("depremler.xlsx")

# 4. Tarih sütununu dönüştür
df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
df = df[df["Date"].notnull()]  # geçersiz tarihleri çıkar
df["Date"] = df["Date"].dt.tz_localize(None)  # varsa timezone'u kaldır

# 5. Gerekirse boş satırları çıkar
df = df.dropna(subset=["Latitude", "Longitude", "Magnitude"])

# 6. Koleksiyonu temizle (isteğe bağlı)
collection.delete_many({})

# 7. MongoDB'ye yükle
collection.insert_many(df.to_dict("records"))

print(f"✅ {len(df)} kayıt başarıyla MongoDB'ye yüklendi.")
