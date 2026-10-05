# Erasmus Scraper — uczelnie w Hiszpanii i Portugalii wg odległości od morza

Skrypt pomaga wybrać uczelnię na wymianę Erasmus+: pobiera listę uczelni z portalu
wyjazdów Politechniki Łódzkiej, geokoduje je, liczy odległość do najbliższej linii
brzegowej i generuje interaktywną mapę z rankingiem.

## Jak to działa

1. **Scraping** — Selenium otwiera `apps.cwm.p.lodz.pl/departures`. Logujesz się i ustawiasz
   filtry ręcznie (poziom, przedmiot, kraje), po czym naciskasz ENTER w konsoli.
   BeautifulSoup wyciąga ze strony nazwy uczelni (szuka słów `University`, `Universidad`,
   `Universidade`, `Politécnica`). Jeśli nic nie znajdzie, wczytuje awaryjną listę przykładową.
2. **Geokodowanie** — Nominatim (OpenStreetMap) zamienia nazwę uczelni na współrzędne.
3. **Odległość do morza** — Overpass API szuka punktów `natural=coastline` w promieniu 300 km
   i wybiera najbliższy; dystans liczony geodezyjnie (geopy).
4. **Mapa** — folium zapisuje `erasmus_map.html`: zielone pinezki < 5 km od morza,
   pomarańczowe < 20 km, niebieskie dalej. Ranking leci też na konsolę.

## Uruchomienie

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python erasmus_spain_portugal.py
```

Potrzebny jest Chrome — `webdriver-manager` sam dociąga odpowiedni chromedriver.

Wynik: `erasmus_map.html` w katalogu uruchomienia. Przykładowy efekt:
[`example_erasmus_map.html`](example_erasmus_map.html).

## Uwagi

- Weryfikacja SSL jest wyłączona (`verify=False`) — obejście typowego problemu
  z certyfikatami w systemowym Pythonie na macOS. Do użytku lokalnego.
- Skrypt robi 1 s przerwy między zapytaniami, żeby nie dostać bana z publicznych API
  Nominatim/Overpass.
- Parser HTML opiera się na słowach kluczowych, nie na strukturze portalu — po zmianie
  layoutu strony trzeba go poprawić.
- Kraj jest zgadywany z nazwy uczelni (`Universidad`/`Universitat` → Hiszpania,
  reszta → Portugalia), więc nietypowe nazwy mogą trafić do złego kraju.
