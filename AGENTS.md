# AGENTS.md

## 1. Cel dokumentu

Ten plik jest instrukcją dla agentów programistycznych pracujących nad lokalną aplikacją webową do konwersji polskich podatkowych ksiąg przychodów i rozchodów (KPiR) z PDF do XLSX.

Szczegółowe uzasadnienie architektury, przepływy danych i kontrakty opisuje [`ARCHITECTURE.md`](./ARCHITECTURE.md). Przed zmianą kodu agent MUSI przeczytać oba dokumenty.

## 2. Ustalony zakres produktu

### MVP

- wejście: tekstowe PDF-y wygenerowane przez programy księgowe; tekst może mieć błędną kolejność po zwykłym kopiowaniu;
- dokument: KPiR, początkowo profil układu odpowiadający dostarczonemu przykładowi z 2018 r.;
- wyjście: znormalizowany XLSX, jeden zapis KPiR na wiersz, poprawne typy dat i liczb;
- dwa tryby:
  - szybka konwersja automatyczna,
  - pełna kontrola: PDF i tabela obok siebie, edycja danych, filtrowanie ostrzeżeń;
- konfigurator przed utworzeniem XLSX:
  - wybór oraz zmiana kolejności eksportowanych kolumn,
  - ręczne wskazanie lub wykluczenie wierszy,
  - filtrowanie wierszy według kwot, co najmniej zakresu „Wydatki razem” od/do,
  - podgląd liczby wierszy i kolumn, które znajdą się w pliku;
- praca z pojedynczym PDF-em i dużymi paczkami obejmującymi tysiące stron;
- działanie lokalne i całkowicie offline;
- uruchamianie deweloperskie skryptem na Windows, macOS i Linux;
- stos: Python + FastAPI, React + TypeScript, SQLite, OpenPyXL.

### Poza MVP

- OCR skanów i zdjęć;
- ogólny konwerter dowolnych tabel;
- wysyłanie dokumentów do chmury lub zewnętrznych API/LLM;
- automatyczne księgowanie albo interpretacja podatkowa;
- współdzielenie instancji przez wielu użytkowników;
- instalator desktopowy.

Nie dodawaj elementów spoza MVP bez osobnej decyzji architektonicznej.

## 3. Nienegocjowalne zasady

1. **Offline oznacza offline.** Kod aplikacji nie może wykonywać żadnych żądań do usług zewnętrznych, pobierać modeli w czasie działania ani wysyłać telemetrii.
2. **Nigdy nie używaj `float` dla kwot.** Kwoty parsuj jako `Decimal`, w bazie zapisuj jako kanoniczny tekst dziesiętny, a do XLSX przekazuj jako liczbę z formatem `0.00`.
3. **Nie poprawiaj danych po cichu.** Wartości wyliczane, np. sumy kolumn, służą do walidacji. Rozbieżność tworzy problem do kontroli; nie wolno bez zgody zastąpić wartości źródłowej.
4. **Każda wartość musi być śledzalna.** Zachowaj identyfikator dokumentu, stronę, prostokąt źródłowy, surowy tekst, wartość sparsowaną, pewność i historię ręcznych zmian.
5. **Oryginał jest niezmienny.** Zaimportowany PDF jest tylko do odczytu. Korekty zapisuj jako osobną warstwę danych.
6. **Błędy jednej strony nie zatrzymują całej paczki.** Zapisz częściowy wynik i czytelny problem dla strony/dokumentu.
7. **Operacje długie są anulowalne i raportują postęp.** Nie wykonuj ekstrakcji ani eksportu dużej paczki w wątku żądania HTTP.
8. **Schemat KPiR jest wersjonowany.** Kolumn i reguł historycznych nie zaszywaj w warstwie UI ani w algorytmie ekstrakcji.
9. **Domyślnie nasłuchuj tylko na loopback.** Backend ma wiązać się z `127.0.0.1`; nie otwieraj aplikacji w LAN bez jawnej konfiguracji.
10. **Eksport jest niedestrukcyjną projekcją danych.** Wybór kolumn, wierszy i filtrów nie zmienia wyników ekstrakcji ani ręcznych korekt.
11. **Filtry korzystają z wartości efektywnych.** Jeśli użytkownik poprawił komórkę, eksport i podgląd filtrują według korekty, a nie starej wartości ekstrakcji.
12. **Zakresy kwot są dokładne i jawne.** Progi parsuj jako `Decimal`; granice `od` i `do` są domyślnie włączne. Pusta kwota nie spełnia aktywnego filtra kwotowego.
13. **Konfiguracja eksportu jest odtwarzalna.** Gotowy eksport zapisuje niezmienny snapshot zakresu, kolumn, filtrów, ręcznego wyboru wierszy i wersji danych.
14. **Język interfejsu MVP: polski.** Identyfikatory w kodzie, komunikaty techniczne i nazwy pól API zapisuj po angielsku.

## 4. Granice modułów

Kod dziel według odpowiedzialności:

- `domain` — encje, typy wartości, profile KPiR, reguły walidacji; bez FastAPI, Reacta i bibliotek PDF;
- `application` — przypadki użycia i porty/interfejsy;
- `infrastructure` — SQLite, system plików, parser PDF, renderer, XLSX;
- `api` — transport HTTP, walidacja wejścia, DTO i mapowanie błędów;
- `worker` — wykonywanie i wznawianie zadań, postęp, anulowanie;
- `web` — prezentacja i interakcje użytkownika.

Warstwa domenowa nie może importować infrastruktury. Parser PDF nie może bezpośrednio zapisywać do SQLite. Endpoint nie może zawierać logiki ekstrakcji.

## 5. Docelowa struktura repozytorium

```text
/
├── AGENTS.md
├── ARCHITECTURE.md
├── README.md
├── pyproject.toml
├── package.json                 # opcjonalne komendy spinające projekt
├── scripts/
│   ├── dev.py                   # wieloplatformowy start API, workera i UI
│   ├── test.py
│   └── clean_local_data.py
├── backend/
│   ├── src/kpir_converter/
│   │   ├── domain/
│   │   │   ├── models.py
│   │   │   ├── values.py
│   │   │   ├── profiles.py
│   │   │   └── validation.py
│   │   ├── application/
│   │   │   ├── commands/
│   │   │   ├── queries/
│   │   │   └── ports/
│   │   ├── infrastructure/
│   │   │   ├── db/
│   │   │   ├── pdf/
│   │   │   ├── storage/
│   │   │   └── xlsx/
│   │   ├── api/
│   │   ├── worker/
│   │   ├── config.py
│   │   └── main.py
│   ├── migrations/
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── contract/
│       ├── golden/
│       └── fixtures/
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── features/
│   │   │   ├── import/
│   │   │   ├── jobs/
│   │   │   ├── review/
│   │   │   └── export/
│   │   ├── components/
│   │   ├── api/
│   │   └── types/
│   └── tests/
├── profiles/
│   └── kpir_pl_2018.yaml
└── docs/
    ├── adr/
    └── test-data-policy.md
```

## 6. Pipeline, którego należy przestrzegać

```text
import → preflight → analiza stron → ekstrakcja słów z pozycjami
→ rozpoznanie profilu → wykrycie kolumn i wierszy
→ przypisanie tokenów → normalizacja → walidacja
→ zapis wyniku i problemów → kontrola użytkownika → eksport XLSX
```

Każdy etap:

- przyjmuje jawny, typowany model wejścia;
- zwraca wynik oraz listę diagnostyk;
- jest deterministyczny dla tych samych danych i wersji profilu;
- okresowo sprawdza flagę anulowania;
- raportuje postęp bez nadmiernych zapisów do bazy;
- nie odrzuca surowych danych potrzebnych do debugowania.

## 7. Konwencje danych

- identyfikatory: UUID;
- daty biznesowe: ISO `YYYY-MM-DD` w API i bazie;
- daty techniczne: UTC, ISO 8601 z `Z`;
- kwoty w JSON: string z kropką, np. `"81.30"`, aby uniknąć utraty precyzji;
- numery stron: API używa indeksu 1-based, wewnętrzne biblioteki mogą używać 0-based tylko przy wyraźnym mapowaniu;
- prostokąty źródłowe: współrzędne znormalizowane do zakresu `0..1` jako `(x0, y0, x1, y1)` względem strony po uwzględnieniu rotacji;
- puste pole i wartość zero to różne stany;
- surowy tekst przechowuj bez destrukcyjnej normalizacji;
- poprawki użytkownika mają `revision`, autora lokalnego `local-user`, czas i opcjonalny powód.

## 8. Reguły ekstrakcji

- korzystaj z tekstu wraz ze współrzędnymi, nie z liniowego `extract_text()`;
- rendering strony służy podglądowi i analizie linii, ale w MVP nie uruchamiaj OCR;
- wykrywaj nagłówki i granice kolumn z użyciem profilu, słów-kluczy oraz elementów rysunkowych PDF;
- uwzględniaj powtarzające się nagłówki, stopki i zmianę strony;
- składanie sztucznie rozstrzelonych liter może działać tylko jako odwracalna transformacja z zachowaniem `raw_text`;
- wynik każdej komórki otrzymuje confidence `0..1` oraz składowe uzasadniające wynik;
- progi pewności muszą być konfigurowalne w profilu;
- nie zakładaj, że wszystkie PDF-y mają identyczny rozmiar strony lub font.

## 9. Walidacja KPiR

Minimum dla MVP:

- wymagane: numer porządkowy, data zdarzenia i numer dowodu, chyba że profil jawnie dopuszcza inaczej;
- data musi istnieć i należeć do deklarowanego okresu dokumentu, jeśli okres został rozpoznany;
- kwoty są nieujemne, o skali maksymalnie dwóch miejsc po przecinku;
- kolumna sumy przychodów jest porównywana z odpowiednimi składnikami;
- kolumna sumy wydatków jest porównywana z odpowiednimi składnikami;
- wykrywanie prawdopodobnych duplikatów bez automatycznego usuwania;
- ciągłość numerów porządkowych jest ostrzeżeniem, nie twardym błędem;
- brak pewności profilu lub granic wiersza blokuje automatyczny eksport tylko zgodnie z polityką eksportu.

Nazwy, zależności i obowiązkowość kolumn definiuje profil. Nie traktuj dokumentu architektonicznego jako porady podatkowej.

## 10. API i UI

- API jest wersjonowane prefiksem `/api/v1` i opisane OpenAPI;
- frontend korzysta wyłącznie z generowanych typów klienta lub kontraktów sprawdzanych testem;
- postęp zadań przekazuj przez SSE; polling jest awaryjny;
- listy wierszy i problemów muszą mieć paginację/wirtualizację;
- zapis edycji stosuje optimistic concurrency przez `revision`; konflikt zwraca HTTP 409;
- panel PDF pokazuje prostokąt źródłowy aktywnej komórki;
- UI musi rozróżniać: wartość wydobytą, poprawioną ręcznie, wyliczoną do kontroli i pustą;
- konfigurator eksportu pozwala wybrać i uporządkować kolumny oraz wybrać/wykluczyć wiersze bez modyfikowania tabeli źródłowej;
- filtr kwotowy MVP pozwala wskazać kolumnę kwotową, minimum i maksimum; domyślną kolumną jest `total_expenses` („Wydatki razem”);
- kolumna użyta w filtrze nie musi być wyeksportowana — np. można filtrować po `total_expenses`, ale pominąć ją w XLSX;
- przed utworzeniem zadania eksportu API zwraca podgląd: liczbę pasujących wierszy, wybrane kolumny, aktywne filtry i problemy blokujące;
- dla dużych paczek wybór wierszy reprezentuj jako zapytanie + listę wyjątków, a nie listę wszystkich identyfikatorów;
- wszystkie akcje muszą być dostępne z klawiatury; nie koduj statusu wyłącznie kolorem.

## 11. Bezpieczeństwo i prywatność

- sprawdzaj sygnaturę `%PDF-`, limit rozmiaru i limit stron; nie ufaj rozszerzeniu;
- pliki zapisuj pod wewnętrznym UUID, nigdy pod przesłaną nazwą;
- odrzucaj path traversal i nie przekazuj nazw plików do powłoki;
- PDF parsuj w osobnym procesie workera z limitami czasu i pamięci, jeśli platforma na to pozwala;
- ustaw ścisłe `Host`/`Origin`, same-origin, CSP bez połączeń zewnętrznych oraz ochronę CSRF dla operacji zmieniających dane;
- logi nie mogą zawierać treści księgowej, nazw kontrahentów ani pełnych ścieżek użytkownika;
- usunięcie dokumentu usuwa oryginał, renderowane strony, dane pośrednie, poprawki i eksporty;
- zależności produkcyjne mają być przypięte lockfile’em i dostępne do instalacji offline z przygotowanego cache’u/wheelhouse’u; samo instalowanie zależności może wymagać internetu na etapie deweloperskim.

## 12. Testy i kryteria jakości

Każda zmiana algorytmu wymaga testów na zanonimizowanych fixture’ach:

- unit: liczby PL, daty, składanie tokenów, geometria, reguły profilu;
- golden: oczekiwane komórki, prostokąty i diagnostyki dla reprezentatywnych stron;
- property-based: formaty kwot, separatory, puste pola i niezmienniki geometrii;
- integration: PDF → baza → skonfigurowany XLSX;
- eksport: projekcja i kolejność kolumn, ręczny wybór wierszy, włączne granice kwot, puste kwoty, wartości po korekcie i filtr po ukrytej kolumnie;
- contract: zgodność OpenAPI i klienta TypeScript;
- UI: edycja, konflikt wersji, filtrowanie problemów, konfigurator eksportu, podgląd liczby wyników i skróty klawiaturowe;
- resilience: anulowanie, restart workera, uszkodzony PDF, błąd pojedynczej strony;
- performance: duża syntetyczna paczka, ograniczona pamięć, stronicowane API oraz wybór „wszystkie pasujące oprócz wykluczonych” bez materializacji wszystkich ID.

Nie aktualizuj golden files automatycznie bez obejrzenia różnicy. Regresja jakości ekstrakcji jest błędem nawet wtedy, gdy testy techniczne przechodzą.

Docelowe bramki jakości:

- formatter i lint bez błędów;
- type checking backendu i frontendu;
- testy deterministyczne i bez dostępu do sieci;
- pokrycie krytycznej domeny i parserów minimum 90%;
- brak sekretów i danych osobowych w repozytorium.

## 13. Praca agenta

Przed implementacją:

1. odczytaj ten plik i `ARCHITECTURE.md`;
2. określ dotknięte granice modułów;
3. sprawdź, czy zmiana wymaga ADR lub migracji bazy;
4. zaplanuj test na realnym, zanonimizowanym przypadku.

Podczas implementacji:

1. twórz małe, odwracalne zmiany;
2. nie mieszaj refaktoryzacji z korektą algorytmu;
3. zapisuj wersję ekstraktora i profilu przy każdym wyniku;
4. aktualizuj kontrakty API i migracje razem z kodem;
5. zachowuj kompatybilność istniejących danych albo dodaj jawną migrację/reprocessing.

Po implementacji agent MUSI podać:

- co zmieniono i dlaczego;
- jakie testy wykonano i z jakim wynikiem;
- wpływ na jakość ekstrakcji i wydajność;
- znane ograniczenia oraz sposób wycofania zmiany;
- czy zmieniono format danych, profil KPiR lub politykę prywatności.

## 14. Definition of Done dla MVP

MVP jest gotowe, gdy użytkownik może lokalnie:

1. uruchomić aplikację jednym skryptem;
2. zaimportować jeden tekstowy PDF KPiR lub paczkę plików;
3. śledzić postęp i anulować zadanie;
4. otrzymać znormalizowane wiersze z informacją o problemach i pewności;
5. kliknąć komórkę i zobaczyć odpowiadający fragment PDF;
6. poprawić dane bez zmiany oryginału;
7. wznowić pracę po restarcie aplikacji;
8. przed eksportem wybrać i uporządkować kolumny, np. pominąć opis zdarzenia i pozostałe przychody;
9. wybrać konkretne wiersze albo zastosować filtr kwotowy `od`/`do`, co najmniej dla „Wydatki razem”;
10. zobaczyć podgląd liczby pasujących wierszy i podsumowanie konfiguracji;
11. wyeksportować XLSX zgodny z tym wyborem, z poprawnymi datami i liczbami;
12. usunąć dokument wraz ze wszystkimi danymi pochodnymi;
13. wykonać cały przepływ bez żadnej komunikacji z internetem.
