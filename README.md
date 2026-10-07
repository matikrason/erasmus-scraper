# Erasmus Scraper — uczelnie w Hiszpanii i Portugalii wg odległości od morza

Skrypt pomaga wybrać uczelnię na wymianę Erasmus+: pobiera listę uczelni z portalu
wyjazdów Politechniki Łódzkiej, geokoduje je, liczy odległość do najbliższej linii
brzegowej i generuje interaktywną mapę z rankingiem.

## Jak to działa

1. **Scraping** — Selenium otwiera `apps.cwm.p.lodz.pl/departures`. Logujesz się i ustawiasz
   filtry ręcznie (poziom, przedmiot, kraje), po czym naciskasz ENTER w konsoli.
   BeautifulSoup zbiera karty uczelni, a `parse_card` wyciąga z nich **nazwę, kraj i miasto** —
   kraj bierze się wprost z portalu, więc nie ma potrzeby zgadywać go z nazwy.
   Jeśli nic nie znajdzie, wczytuje awaryjną listę przykładową. Strona jest zapisywana
   do `page_dump.html`, co ułatwia poprawianie parsera.
2. **Geokodowanie** — [Photon](https://photon.komoot.io) (dane OSM, bez klucza i bez sztywnego
   limitu). Szukane jest **miasto z karty, nie nazwa uczelni**: angielskie nazwy z portalu słabo
   pasują do OSM, przez co „University of Alicante" trafiało na pole golfowe w Elche.
   Najpierw `place:city`, potem `place:town`/`village` — inaczej „León" ląduje w wiosce
   Calera de León. Dokładność jest do centrum miasta.
3. **Odległość do morza** — liczona **lokalnie, bez żadnego API**, z pliku
   [Natural Earth 1:10m](https://github.com/nvkelso/natural-earth-vector) (~10 MB, pobierany
   raz i cache'owany obok skryptu). Najbliższy punkt wybierany jest tanim przybliżeniem
   płaskim, a dokładny dystans geodezyjny (geopy) liczony tylko raz — dla zwycięzcy.
4. **Mapa** — folium zapisuje `erasmus_map.html`: zielone pinezki < 15 km od morza,
   pomarańczowe < 80 km, niebieskie dalej. Ranking leci też na konsolę.

## Uruchomienie

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python erasmus_spain_portugal.py
```

Potrzebny jest Chrome — `webdriver-manager` sam dociąga odpowiedni chromedriver.

Wynik: `erasmus_map.html` w katalogu uruchomienia. Przykładowy efekt dla awaryjnej listy
sześciu uczelni: [`example_erasmus_map.html`](example_erasmus_map.html).

### Opcje

| flaga | domyślnie | opis |
| --- | --- | --- |
| `--output PLIK` | `erasmus_map.html` | gdzie zapisać mapę |
| `--url ADRES` | portal PŁ | inny adres listy wyjazdów |
| `--insecure` | wyłączone | wyłącza weryfikację SSL (obejście braku certyfikatów w systemowym Pythonie na macOS) |

## Uwagi i ograniczenia

- **Dokładność.** Natural Earth 1:10m ma rozdzielczość ~1 km, a geokoder celuje w centrum
  miasta — wystarczy do rankingu, nie do metrów. Dla porównania, ta sama trójka liczona
  wcześniej z Overpass API: Lizbona 4,86 vs 5,08 km, León 99,95 vs 99,7 km,
  Madryt 309,44 vs 306,5 km.
- **Dlaczego lokalnie, a nie przez API.** Wcześniejsza wersja pytała Overpass API o linię
  brzegową w rosnącym promieniu. Publiczne instancje limitują per IP i zwracały 429/504
  nawet po ponawianiu i przełączaniu na mirrory, a jedna uczelnia potrafiła zająć minuty.
  Lokalny plik liczy cały ranking w zasadzie natychmiast.
- **Photon potrafi być wolny.** Publiczna instancja odpowiada czasem w 7 s, a czasem
  w 25 s i więcej, dlatego timeout jest ustawiony na 90 s. Przy krótszym padają wszystkie
  zapytania i skrypt kończy bez ani jednej uczelni.
- **Parser HTML opiera się na słowach kluczowych**, nie na strukturze portalu — po zmianie
  layoutu strony trzeba go poprawić. `page_dump.html` jest po to, żeby było z czego.
- **SSL** jest weryfikowany domyślnie. `--insecure` istnieje tylko jako obejście
  lokalnego problemu z certyfikatami na macOS i wyłącza weryfikację dla wszystkich
  zapytań, także pobierania chromedrivera.
