"""Ranking uczelni Erasmus+ (Hiszpania / Portugalia) według odległości od morza.

Skrypt otwiera portal wyjazdów PŁ, pozwala ręcznie ustawić filtry, wyciąga z kart
nazwę / kraj / miasto uczelni, geokoduje miasto w Photonie i liczy odległość do
najbliższego punktu linii brzegowej lokalnie, z pliku Natural Earth.
"""

import argparse
import html
import json
import math
import os
import re
import ssl
import time
from functools import lru_cache

import folium
import requests
import urllib3
from bs4 import BeautifulSoup
from geopy.distance import geodesic
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

PORTAL_URL = "https://apps.cwm.p.lodz.pl/departures"
PHOTON_URL = "https://photon.komoot.io/api/"
USER_AGENT = "erasmus_lodz_scraper/1.0 (michalmuller@onet.eu)"

# Publiczna instancja Photona potrafi odpowiadać 25 s i dłużej — przy krótkim
# timeoucie padają wszystkie zapytania i skrypt kończy bez ani jednej uczelni.
PHOTON_TIMEOUT = 90
PHOTON_DELAY = 1.0

COAST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ne_10m_coastline.geojson')
COAST_URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson'
# Półwysep Iberyjski wraz z Kanarami i Maderą
COAST_BBOX = (25, 48, -20, 8)  # lat_min, lat_max, lon_min, lon_max

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def configure_session(insecure):
    if insecure:
        # Obejście braku certyfikatów root w systemowym Pythonie na macOS.
        _session.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        ssl._create_default_https_context = ssl._create_unverified_context
        os.environ["WDM_SSL_VERIFY"] = "0"
        print("UWAGA: weryfikacja certyfikatów SSL wyłączona (--insecure).\n")


def parse_card(text):
    """'Erasmus+Code:E LEON01University of LeonSpain •LeónDetails...' -> (nazwa, kraj, miasto)"""
    text = re.sub(r'Erasmus\+Code:\s*[A-Z]\s*[A-Z\-]+\d+', '', text)
    m = re.match(r'(.+?)\s*(Spain|Portugal)\s*•?\s*(.*)', text)
    if not m:
        return None
    city = re.split(r'Details|University website|Additional information', m.group(3))[0].strip()
    return m.group(1).strip(), m.group(2), city


def _download_coastline():
    """Pobiera plik do .part i podmienia dopiero po sukcesie — przerwane albo
    błędne pobranie nie zostawi uszkodzonego cache'u, który psuje każdy kolejny run."""
    print("Pobieram linię brzegową (jednorazowo, ~10 MB)...")
    tmp_path = COAST_FILE + '.part'
    response = _session.get(COAST_URL, timeout=180, stream=True)
    response.raise_for_status()
    try:
        with open(tmp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1 << 16):
                f.write(chunk)
        with open(tmp_path, encoding='utf-8') as f:  # sanity check, zanim zostanie cache'm
            json.load(f)
        os.replace(tmp_path, COAST_FILE)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _iter_coords(geometry):
    """Natural Earth jest pobierany z gałęzi master, więc nie zakładamy, że wszystkie
    obiekty to LineString."""
    coords = geometry.get('coordinates') or []
    if geometry.get('type') == 'MultiLineString':
        for line in coords:
            yield from line
    else:
        yield from coords


@lru_cache(maxsize=1)
def coast_points():
    """Punkty linii brzegowej (Natural Earth 1:10m) wokół Półwyspu Iberyjskiego, z Kanarami i Maderą."""
    if not os.path.exists(COAST_FILE):
        _download_coastline()
    with open(COAST_FILE, encoding='utf-8') as f:
        features = json.load(f)['features']
    lat_min, lat_max, lon_min, lon_max = COAST_BBOX
    return [(lat, lon) for feat in features for lon, lat in _iter_coords(feat['geometry'])
            if lat_min < lat < lat_max and lon_min < lon < lon_max]


def get_distance_to_coast(lat, lon):
    """
    Odległość do najbliższego punktu linii brzegowej, liczona lokalnie (bez API).
    Dokładność Natural Earth 1:10m to ~1 km — wystarczy do rankingu, nie do metrów.
    """
    k = math.cos(math.radians(lat))  # przybliżenie płaskie tylko do wyboru kandydata
    nearest = min(coast_points(), key=lambda p: (p[0] - lat) ** 2 + ((p[1] - lon) * k) ** 2)
    return round(geodesic((lat, lon), nearest).kilometers, 2)


def photon(q, **params):
    """Jedno zapytanie do Photon (komoot, dane OSM) — bez klucza i bez sztywnego limitu. Zwraca (lat, lon, adres) albo None."""
    response = _session.get(PHOTON_URL, params={"q": q, "limit": 1, **params},
                            timeout=PHOTON_TIMEOUT)
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
    Dokładność do centrum miasta; kampus poza miastem (np. Leioa) i tak ma własne miasto na karcie.
    """
    try:
        if city:
            # najpierw miasta — inaczej "León" trafia w wioskę "Calera de León"
            for tags in (["place:city"], ["place:town", "place:village"]):
                hit = photon(f"{city}, {country}", osm_tag=tags)
                if hit:
                    return hit
        return photon(f"{university_name}, {country}") or (None, None, None)
    except requests.RequestException as e:
        print(f"   [Błąd Photon]: {e}")
    return None, None, None


FALLBACK_UNIVERSITIES = [
    ("Universidad Politécnica de Madrid", "Spain", "Madrid"),
    ("Universitat Politècnica de València", "Spain", "Valencia"),
    ("Universidade de Lisboa", "Portugal", "Lisboa"),
    ("Universidade do Porto", "Portugal", "Porto"),
    ("Universidad de Málaga", "Spain", "Málaga"),
    ("Universidad de Alicante", "Spain", "Alicante"),
]


def scrape_universities(url, dump_path='page_dump.html'):
    """Otwiera portal, czeka na ręczne ustawienie filtrów i zwraca karty (nazwa, kraj, miasto)."""
    print("Uruchamianie przeglądarki...")
    options = webdriver.ChromeOptions()
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    try:
        driver.get(url)
        print("\n" + "=" * 60)
        print(" UWAGA: Przeglądarka została otwarta.")
        print(" 1. Zaloguj się (jeśli portal PŁ tego wymaga).")
        print(" 2. Ustaw filtry: Poziom (Inżynier), Przedmiot (Informatyka), Kraje (Hiszpania, Portugalia).")
        print(" 3. Gdy lista uczelni pojawi się na ekranie, wróć tutaj do konsoli.")
        print("=" * 60 + "\n")
        input("Naciśnij [ENTER], gdy lista uczelni będzie już załadowana na stronie...")

        print("Pobieranie danych ze strony...")
        page_html = driver.page_source
    finally:
        # bez finally wyjątek w trakcie scrapowania zostawia otwarty Chrome
        driver.quit()

    with open(dump_path, 'w', encoding='utf-8') as f:
        f.write(page_html)  # zrzut strony do debugowania parsera

    soup = BeautifulSoup(page_html, 'html.parser')
    # Nie znamy struktury HTML portalu, więc szukamy słów kluczowych w komórkach
    keywords = ('universidad', 'university', 'universidade', 'politécnica', 'politecnica')
    universities = set()
    for cell in soup.find_all(['td', 'th', 'div', 'span', 'a']):
        text = cell.get_text(strip=True)
        if not (8 < len(text) < 150) or not any(kw in text.lower() for kw in keywords):
            continue
        card = parse_card(text)
        if card and len(card[0]) > 5:
            universities.add(card)
    return sorted(universities)


def build_map(results, output_path):
    print("\nGenerowanie mapy...")
    # Esri zamiast domyślnego OSM — kafelki OSM blokują strony otwierane z pliku (403),
    # CartoDB wymaga klucza API
    folium_map = folium.Map(location=[40.0, -4.0], zoom_start=6,  # Półwysep Iberyjski
                            tiles="Esri.WorldStreetMap")
    for idx, res in enumerate(results, start=1):
        dist = res['distance_to_sea']
        if dist < 15:
            color, icon_type = 'green', 'tint'      # bardzo blisko wody
        elif dist < 80:
            color, icon_type = 'orange', 'tint'     # w miarę blisko
        else:
            color, icon_type = 'blue', 'info-sign'  # daleko od wody

        popup_text = (
            f"<b>{html.escape(res['name'])}</b><br>"
            f"Adres: {html.escape(res['address'] or res['country'])}<br>"
            f"Ranking odległości: #{idx}<br>"
            f"Odległość do morza: {dist} km"
        )
        folium.Marker(
            location=[res['lat'], res['lon']],
            popup=folium.Popup(popup_text, max_width=320),
            tooltip=res['name'],
            icon=folium.Icon(color=color, icon=icon_type),
        ).add_to(folium_map)

    folium_map.save(output_path)
    print(f"\n✅ Mapa została zapisana w pliku: {output_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="erasmus_map.html", help="plik wynikowy z mapą")
    parser.add_argument("--url", default=PORTAL_URL, help="adres portalu wyjazdów")
    parser.add_argument("--insecure", action="store_true",
                        help="wyłącz weryfikację SSL (obejście braku certyfikatów na macOS)")
    args = parser.parse_args()

    configure_session(args.insecure)

    universities = scrape_universities(args.url)
    if universities:
        print(f"Znaleziono {len(universities)} potencjalnych uczelni: "
              f"{', '.join(u[0] for u in universities)}")
    else:
        print("\nNie udało się automatycznie pobrać listy ze strony (niestandardowa struktura HTML).")
        print("Trwa ładowanie listy testowej / awaryjnej...")
        universities = FALLBACK_UNIVERSITIES

    results = []
    print("\nRozpoczynam analizę geograficzną (to może chwilę potrwać)...\n")
    for uni, country, city in universities:
        print(f"-> Przetwarzam: {uni} ({city})")
        lat, lon, address = get_coordinates(uni, city, country)
        if lat is None or lon is None:
            print(f"   Nie znaleziono lokalizacji dla: {uni}")
            continue
        results.append({
            'name': uni,
            'country': country,
            'address': address,
            'lat': lat,
            'lon': lon,
            'distance_to_sea': get_distance_to_coast(lat, lon),
        })
        time.sleep(PHOTON_DELAY)  # nie dobijamy publicznej instancji Photona

    if not results:
        print("Brak danych do wygenerowania mapy.")
        return

    results.sort(key=lambda res: res['distance_to_sea'])
    build_map(results, args.output)

    print("\n" + "=" * 50)
    print(" RANKING UCZELNI (OD NAJBLIŻSZEJ MORZA):")
    print("=" * 50)
    for idx, res in enumerate(results, start=1):
        print(f"{idx}. {res['name']} ({res['country']}) -> {res['distance_to_sea']} km od oceanu/morza")
    print("=" * 50)


if __name__ == "__main__":
    main()
