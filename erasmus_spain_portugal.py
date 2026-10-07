import time
import requests
import re
import ssl
import os
import json
import math
from functools import lru_cache
from bs4 import BeautifulSoup
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import folium

# Wyłączenie weryfikacji SSL (częsty problem na macOS w domyślnym Pythonie)
ssl._create_default_https_context = ssl._create_unverified_context
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

def parse_card(text):
    """'Erasmus+Code:E LEON01University of LeonSpain •LeónDetails...' -> (nazwa, kraj, miasto)"""
    text = re.sub(r'Erasmus\+Code:\s*[A-Z]\s*[A-Z\-]+\d+', '', text)
    m = re.match(r'(.+?)\s*(Spain|Portugal)\s*•?\s*(.*)', text)
    if not m:
        return None
    city = re.split(r'Details|University website|Additional information', m.group(3))[0].strip()
    return m.group(1).strip(), m.group(2), city

COAST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ne_10m_coastline.geojson')
COAST_URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson'

@lru_cache(maxsize=1)
def coast_points():
    """Punkty linii brzegowej (Natural Earth 1:10m) wokół Półwyspu Iberyjskiego, z Kanarami i Maderą."""
    if not os.path.exists(COAST_FILE):
        print("Pobieram linię brzegową (jednorazowo, ~10 MB)...")
        with open(COAST_FILE, 'wb') as f:
            f.write(requests.get(COAST_URL, timeout=120).content)
    with open(COAST_FILE, encoding='utf-8') as f:
        features = json.load(f)['features']
    return [(lat, lon) for feat in features for lon, lat in feat['geometry']['coordinates']
            if 25 < lat < 48 and -20 < lon < 8]

def get_distance_to_coast(lat, lon):
    """
    Odległość do najbliższego punktu linii brzegowej, liczona lokalnie (bez API).
    ponytail: dokładność Natural Earth 1:10m to ~1 km — wystarczy do rankingu, nie do metrów.
    """
    k = math.cos(math.radians(lat))  # przybliżenie płaskie tylko do wyboru kandydata
    nearest = min(coast_points(), key=lambda p: (p[0] - lat) ** 2 + ((p[1] - lon) * k) ** 2)
    return round(geodesic((lat, lon), nearest).kilometers, 2)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from geopy.adapters import RequestsAdapter

class UnverifiedRequestsAdapter(RequestsAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session.verify = False

def photon(q, **params):
    """Jedno zapytanie do Photon (komoot, dane OSM) — bez klucza i bez sztywnego limitu. Zwraca (lat, lon, adres) albo None."""
    headers = {"User-Agent": "erasmus_lodz_scraper/1.0 (michalmuller@onet.eu)"}
    # Złote narzędzie verify=False eliminuje jakiekolwiek sprawdzanie certyfikatów
    response = requests.get("https://photon.komoot.io/api/", params={"q": q, "limit": 1, **params},
                            headers=headers, verify=False, timeout=10)
    response.raise_for_status()
    features = response.json().get('features')
    if not features:
        return None
    lon, lat = features[0]['geometry']['coordinates']  # GeoJSON: [lon, lat]
    p = features[0]['properties']
    return float(lat), float(lon), ", ".join(filter(None, [p.get('name'), p.get('city'), p.get('country')]))

def get_coordinates(university_name, city, country):
    """
    Geokodujemy miasto z karty, nie nazwę uczelni — angielskie nazwy z portalu słabo pasują do OSM
    (np. "University of Alicante" trafiało na pole golfowe w Elche).
    ponytail: dokładność do centrum miasta; kampus poza miastem (np. Leioa) i tak ma własne miasto na karcie.
    """
    try:
        if city:
            # najpierw miasta — inaczej "León" trafia w wioskę "Calera de León"
            for tags in (["place:city"], ["place:town", "place:village"]):
                hit = photon(f"{city}, {country}", osm_tag=tags)
                if hit:
                    return hit
        return photon(f"{university_name}, {country}") or (None, None, None)
    except Exception as e:
        print(f"   [Błąd Photon]: {e}")
    return None, None, None

def main():
    # 1. Uruchomienie przeglądarki
    print("Uruchamianie przeglądarki...")
    options = webdriver.ChromeOptions()
    # options.add_argument('--headless') # Odkomentuj, jeśli nie chcesz widzieć okna przeglądarki
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    url = "https://apps.cwm.p.lodz.pl/departures"
    driver.get(url)
    
    print("\n" + "="*60)
    print(" UWAGA: Przeglądarka została otwarta.")
    print(" 1. Zaloguj się (jeśli portal PŁ tego wymaga).")
    print(" 2. Ustaw filtry: Poziom (Inżynier), Przedmiot (Informatyka), Kraje (Hiszpania, Portugalia).")
    print(" 3. Gdy lista uczelni pojawi się na ekranie, wróć tutaj do konsoli.")
    print("="*60 + "\n")
    
    input("Naciśnij [ENTER], gdy lista uczelni będzie już załadowana na stronie...")
    
    # 2. Pobieranie danych ze strony (Scraping)
    print("Pobieranie danych ze strony...")
    html = driver.page_source
    with open('page_dump.html', 'w', encoding='utf-8') as f:
        f.write(html)  # zrzut strony do debugowania parsera
    soup = BeautifulSoup(html, 'html.parser')
    
    universities = set() # Używamy zbioru, by uniknąć duplikatów
    
    # Ponieważ nie znamy dokładnej struktury HTML portalu, 
    # szukamy typowych słów kluczowych w komórkach tabeli i nagłówkach
    keywords = ['Universidad', 'University', 'Universidade', 'Politécnica', 'Politecnica']
    
    # Metoda A: Szukanie w standardowych tabelach
    for cell in soup.find_all(['td', 'th', 'div', 'span', 'a']):
        text = cell.get_text(strip=True)
        if any(kw.lower() in text.lower() for kw in keywords) and len(text) > 8 and len(text) < 150:
            card = parse_card(text)
            if card and len(card[0]) > 5:
                universities.add(card)
            
    driver.quit()
    
    # Jeśli skrypt nic nie znajdzie (struktura strony jest ukryta), możesz wpisać listę ręcznie
    if not universities:
        print("\nNie udało się automatycznie pobrać listy ze strony (niestandardowa struktura HTML).")
        print("Trwa ładowanie listy testowej / awaryjnej...")
        # Przykładowe uczelnie z Hiszpanii i Portugalii na kierunkach CS:
        universities = [
            ("Universidad Politécnica de Madrid", "Spain", "Madrid"),
            ("Universitat Politècnica de València", "Spain", "Valencia"),
            ("Universidade de Lisboa", "Portugal", "Lisboa"),
            ("Universidade do Porto", "Portugal", "Porto"),
            ("Universidad de Málaga", "Spain", "Málaga"),
            ("Universidad de Alicante", "Spain", "Alicante"),
        ]
    else:
        print(f"Znaleziono {len(universities)} potencjalnych uczelni: {', '.join(u[0] for u in universities)}")

    # 3. Przetwarzanie uczelni (Geokodowanie i dystans)
    results = []
    print("\nRozpoczynam analizę geograficzną (to może chwilę potrwać)...\n")
    
    for uni, country, city in universities:
        print(f"-> Przetwarzam: {uni} ({city})")

        lat, lon, address = get_coordinates(uni, city, country)
        
        if lat and lon:
            distance = get_distance_to_coast(lat, lon)
            results.append({
                'name': uni,
                'country': country,
                'address': address,
                'lat': lat,
                'lon': lon,
                'distance_to_sea': distance
            })
            time.sleep(1) # Przerwa, żeby nie zablokowały nas API
        else:
            print(f"   Nie znaleziono lokalizacji dla: {uni}")

    # 4. Sortowanie od najbliższej do morza do najdalszej
    results_sorted = sorted(results, key=lambda x: x['distance_to_sea'])

    # 5. Generowanie mapy
    if results_sorted:
        print("\nGenerowanie mapy...")
        # Środek mapy (Półwysep Iberyjski)
        # Esri zamiast domyślnego OSM — kafelki OSM blokują strony otwierane z pliku (403), CartoDB wymaga klucza API
        m = folium.Map(location=[40.0, -4.0], zoom_start=6, tiles="Esri.WorldStreetMap")
        
        for idx, res in enumerate(results_sorted):
            name = res['name']
            dist = res['distance_to_sea']
            
            # Kolorowanie:
            if dist < 15:
                color = 'green'      # Bardzo blisko wody
                icon_type = 'tint'   # Ikonka kropli
            elif dist < 80:
                color = 'orange'     # W miarę blisko
                icon_type = 'tint'
            else:
                color = 'blue'       # Daleko od wody (neutralny)
                icon_type = 'info-sign'
                
            address_str = res.get('address', res['country'])
            popup_text = f"<b>{name}</b><br>Adres: {address_str}<br>Ranking odległości: #{idx+1}<br>Odległość do morza: {dist} km"
            
            folium.Marker(
                location=[res['lat'], res['lon']],
                popup=popup_text,
                icon=folium.Icon(color=color, icon=icon_type)
            ).add_to(m)
            
        map_filename = 'erasmus_map.html'
        m.save(map_filename)
        print(f"\n✅ Mapa została zapisana w pliku: {map_filename}")
        
        # 6. Wyświetlenie wyników w konsoli
        print("\n" + "="*50)
        print(" RANKING UCZELNI (OD NAJBLIŻSZEJ MORZA):")
        print("="*50)
        for idx, res in enumerate(results_sorted):
            print(f"{idx+1}. {res['name']} ({res['country']}) -> {res['distance_to_sea']} km od oceanu/morza")
        print("="*50)
    else:
        print("Brak danych do wygenerowania mapy.")

if __name__ == "__main__":
    main()