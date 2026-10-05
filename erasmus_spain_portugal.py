import time
import requests
import re
import ssl
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

def clean_university_name(text):
    # Odetniemy dokładny kod np. "Erasmus+Code:E LEON01"
    text = re.sub(r'Erasmus\+Code:\s*[A-Z]\s*[A-Z]+\d+', '', text)
    text = text.replace('Spain', ' Spain').replace('Portugal', ' Portugal')
    if ' Spain' in text:
        text = text.split(' Spain')[0]
    if ' Portugal' in text:
        text = text.split(' Portugal')[0]
    
    text = text.replace('Details', '').replace('University website', '').replace('Additional information', '').strip()
    return text

def get_distance_to_coast(lat, lon):
    """
    Łączy się z Overpass API (OpenStreetMap), aby znaleźć najbliższą linię brzegową
    w promieniu do 300 km i oblicza do niej odległość.
    """
    print(f"      Szukam najbliższego wybrzeża dla współrzędnych {lat}, {lon}...")
    overpass_url = "http://overpass-api.de/api/interpreter"
    
    # Zapytanie szukające punktów linii brzegowej w promieniu 300km (300000 metrów)
    overpass_query = f"""
    [out:json];
    way["natural"="coastline"](around:300000,{lat},{lon});
    out geom;
    """
    
    try:
        response = requests.post(overpass_url, data={'data': overpass_query}, timeout=15)
        data = response.json()
        
        if not data.get('elements'):
            return float('inf') # Jeśli nie znajdzie morza w promieniu 300km
            
        min_dist = float('inf')
        
        # Przeszukiwanie węzłów linii brzegowej, aby znaleźć ten najbliższy
        for element in data['elements']:
            if 'geometry' in element:
                for node in element['geometry']:
                    coast_lat = node['lat']
                    coast_lon = node['lon']
                    dist = geodesic((lat, lon), (coast_lat, coast_lon)).kilometers
                    if dist < min_dist:
                        min_dist = dist
                        
        return round(min_dist, 2)
    except Exception as e:
        print(f"      [Błąd API Overpass]: {e}")
        return float('inf')

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from geopy.adapters import RequestsAdapter

class UnverifiedRequestsAdapter(RequestsAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session.verify = False

def get_coordinates(university_name, country):
    """
    Łączy się bezpośrednio z OpenStreetMap (Nominatim), całkowicie omijając lokalne problemy z SSL.
    """
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "erasmus_lodz_scraper"}
    
    # Zapytanie ze wskazanym krajem
    params = {"q": f"{university_name}, {country}", "format": "json", "limit": 1, "addressdetails": 1}
    try:
        # Złote narzędzie verify=False eliminuje jakiekolwiek sprawdzanie certyfikatów
        response = requests.get(url, params=params, headers=headers, verify=False, timeout=10)
        data = response.json()
        
        if data:
            return float(data[0]['lat']), float(data[0]['lon']), data[0].get('display_name', '')
        
        # Spróbuj wyszukać samą nazwę, bez kraju
        params['q'] = university_name
        response = requests.get(url, params=params, headers=headers, verify=False, timeout=10)
        data = response.json()
        
        if data:
            return float(data[0]['lat']), float(data[0]['lon']), data[0].get('display_name', '')
            
    except Exception as e:
        pass
        
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
    soup = BeautifulSoup(html, 'html.parser')
    
    universities = set() # Używamy zbioru, by uniknąć duplikatów
    
    # Ponieważ nie znamy dokładnej struktury HTML portalu, 
    # szukamy typowych słów kluczowych w komórkach tabeli i nagłówkach
    keywords = ['Universidad', 'University', 'Universidade', 'Politécnica', 'Politecnica']
    
    # Metoda A: Szukanie w standardowych tabelach
    for cell in soup.find_all(['td', 'th', 'div', 'span', 'a']):
        text = cell.get_text(strip=True)
        if any(kw.lower() in text.lower() for kw in keywords) and len(text) > 8 and len(text) < 100:
            cleaned = clean_university_name(text)
            if cleaned and len(cleaned) > 5 and 'website' not in cleaned.lower() and 'details' not in cleaned.lower():
                universities.add(cleaned)
            
    driver.quit()
    
    # Jeśli skrypt nic nie znajdzie (struktura strony jest ukryta), możesz wpisać listę ręcznie
    if not universities:
        print("\nNie udało się automatycznie pobrać listy ze strony (niestandardowa struktura HTML).")
        print("Trwa ładowanie listy testowej / awaryjnej...")
        # Przykładowe uczelnie z Hiszpanii i Portugalii na kierunkach CS:
        universities = [
            "Universidad Politécnica de Madrid", 
            "Universitat Politècnica de València",
            "Universidade de Lisboa",
            "Universidade do Porto",
            "Universidad de Málaga",
            "Universidad de Alicante"
        ]
    else:
        print(f"Znaleziono {len(universities)} potencjalnych uczelni: {', '.join(universities)}")

    # 3. Przetwarzanie uczelni (Geokodowanie i dystans)
    results = []
    print("\nRozpoczynam analizę geograficzną (to może chwilę potrwać)...\n")
    
    for uni in universities:
        print(f"-> Przetwarzam: {uni}")
        # Ustalamy kraj na podstawie nazwy lub domyślnie szukamy w obu
        country = "Spain" if "Universidad" in uni or "Universitat" in uni else "Portugal"
        
        lat, lon, address = get_coordinates(uni, country)
        
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
        m = folium.Map(location=[40.0, -4.0], zoom_start=6)
        
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