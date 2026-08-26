# Kreator Zestawów BaseLinker

Aplikacja webowa (Flask + panel w przeglądarce) do masowego tworzenia
**zestawów (bundle)** w katalogu BaseLinker na podstawie pliku CSV. To
rozwinięcie Twojego oryginalnego skryptu: ta sama logika biznesowa, ale
poprawiona zgodnie z aktualną dokumentacją API BaseLinkera, ułożona w
klasy i połączona z prostym interfejsem w przeglądarce — bez terminala.

## Co zostało poprawione względem oryginalnego skryptu

Sprawdziłem wszystkie użyte zapytania w oficjalnej dokumentacji
`api.baselinker.com`. Kilka rzeczy w oryginalnym skrypcie faktycznie by
nie zadziałało:

1. **Zły adres API.** Skrypt wysyłał zapytania na `https://baselinker.com`
   zamiast `https://api.baselinker.com/connector.php`. Bez tego żadne
   zapytanie by się nie powiodło.
2. **Brak flagi `is_bundle: true`.** Bez niej BaseLinker ignoruje pole
   `bundle_products` — powstałby zwykły produkt, a nie zestaw.
3. **Nazwa i cechy produktu w złym miejscu.** API oczekuje nazwy w
   `text_fields.name`, a cech (parametrów) w `text_fields.features` jako
   płaskiego słownika `{"Kolor": "Czerwony"}`, a nie jako osobnych pól
   najwyższego poziomu `name` / `parameters` z zagnieżdżonym
   `{"name": ..., "value": ...}`.
4. **Odczyt cech składników** czytał z nieistniejącego klucza
   `parameters` zamiast `text_fields.features` w odpowiedzi
   `getInventoryProductsData` — w oryginale zawsze zwracał pusty słownik.
5. **Brak zabezpieczenia przed złą ilością w CSV.** Literówka w kolumnie
   `ILOSC_x` (tekst zamiast liczby) wywalała cały skrypt wyjątkiem. Taki
   wiersz jest teraz pomijany z czytelnym komunikatem.
6. **Throttling tylko między wierszami.** BaseLinker pozwala na 100
   zapytań/minutę. Jeden wiersz z 5 składnikami to nawet 7 zapytań do
   API — przy większych plikach odstęp 0.3 s tylko między wierszami mógł
   nie wystarczyć. Teraz dławione jest każde pojedyncze zapytanie (0.65 s
   odstępu), a wyszukane SKU i pobrane cechy są cache'owane na czas
   trwania importu, więc powtarzające się składniki w wielu zestawach nie
   generują zbędnych zapytań.

Cena zestawu nadal trafia na `0,00` — tak jak w oryginale, do
uzupełnienia później w panelu BaseLinker (adres grupy cenowej możesz
teraz ustawić w interfejsie zamiast na sztywno w kodzie).

## Wymagania

- Python 3.9 lub nowszy
- pip

## Instalacja i uruchomienie

```bash
cd baselinker_bundler
pip install -r requirements.txt
python app.py
```

Następnie otwórz w przeglądarce: **http://localhost:5050**

(Nie 5000 — na macOS ten port bywa zajęty przez systemowy AirPlay Receiver
i zwraca wtedy błąd 403 zamiast strony aplikacji.)

Aplikacja działa wyłącznie lokalnie na Twoim komputerze — jedyne
połączenie sieciowe, jakie nawiązuje, to zapytania do
`api.baselinker.com` wykonywane w Twoim imieniu.

## Jak korzystać

1. W panelu **Ustawienia** wklej token API BaseLinker oraz ID katalogu
   (`inventory_id`, najczęściej `1`). Opcjonalnie kliknij „Testuj
   połączenie”, żeby upewnić się, że dane są poprawne.
2. Przeciągnij plik CSV z zestawami na pole w kroku 1 (albo kliknij, aby
   wybrać plik z dysku). W kroku 2 zobaczysz podgląd pierwszych 5
   wierszy.
3. Sprawdź, czy dane wyglądają dobrze — wiersze z brakującą nazwą lub SKU
   są podświetlone na pomarańczowo.
4. Kliknij **„Rozpocznij tworzenie zestawów”** i obserwuj log na żywo w
   kroku 3. W dowolnym momencie możesz przerwać przyciskiem „Anuluj”.
5. Po zakończeniu zobaczysz podsumowanie: ile zestawów utworzono, ile
   pominięto i ile było błędów. Log da się pobrać jako plik `.txt`.

## Format pliku CSV

Separator: **średnik `;`**. Kodowanie UTF-8 lub Windows-1250 (typowe dla
Excela) — oba są wykrywane automatycznie.

| Kolumna | Wymagana | Opis |
|---|---|---|
| `NAZWA` | tak | Nazwa zestawu |
| `SKU` | tak | SKU zestawu |
| `EAN` | nie | EAN zestawu |
| `SKU_1` … `SKU_5` | nie | SKU składnika (do 5 składników na zestaw) |
| `ILOSC_1` … `ILOSC_5` | nie | Ilość danego składnika (domyślnie 1) |

Przykładowy plik możesz pobrać bezpośrednio z aplikacji (link w panelu
bocznym, pod instrukcją) — to ten sam plik co `przyklad_zestawy.csv` w
tym folderze.

## Struktura projektu

```
baselinker_bundler/
├── app.py                  # backend Flask + klasy BaseLinkerClient / BundleImportJob
├── przyklad_zestawy.csv    # przykładowy / wzorcowy plik CSV
├── requirements.txt
├── templates/
│   └── index.html
└── static/
    ├── css/style.css
    └── js/app.js
```

`BaseLinkerClient` i `BundleImportJob` w `app.py` są niezależne od
Flaska — jeśli kiedyś zechcesz wrócić do wersji terminalowej albo
uruchamiać import z crona bez przeglądarki, możesz zaimportować te dwie
klasy do własnego, prostego skryptu.

## Uwagi

- Token API daje szeroki dostęp do konta BaseLinker — traktuj go jak
  hasło. Checkbox „Zapamiętaj dane na tym urządzeniu” zapisuje go
  wyłącznie w pamięci Twojej przeglądarki (`localStorage`) i jest
  domyślnie wyłączony.
- To wciąż serwer deweloperski Flask (zobaczysz jego ostrzeżenie w
  konsoli) — w pełni wystarczający do jednoosobowego, lokalnego użytku,
  do którego to narzędzie zostało pomyślane.
- Limit BaseLinkera to 100 zapytań/minutę. Bardzo duże pliki (setki
  zestawów z wieloma unikalnymi składnikami) będą przetwarzane
  proporcjonalnie dłużej — to celowe zabezpieczenie, nie błąd.


## Instalacja i uruchomienie
```bash
cd ~/Desktop/baselinker_bundler
python3 -m pip install -r requirements.txt
python3 app.py
```
```bash
http://localhost:5050# baselinker_bundler
```
