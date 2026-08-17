# Architektura lokalnego konwertera KPiR PDF → Excel

**Status:** propozycja bazowa do implementacji  
**Wersja dokumentu:** 1.1  
**Data:** 2026-08-17  
**Powiązany dokument:** [`AGENTS.md`](./AGENTS.md)

## 1. Streszczenie

Aplikacja jest lokalnym systemem webowym, który wydobywa rekordy z tekstowych plików PDF KPiR, normalizuje je do wersjonowanego schematu, pozwala porównać każdą komórkę z miejscem w źródłowym PDF-ie i eksportuje zatwierdzone dane do XLSX.

Rekomendowana architektura to **modularny monolit z osobnym procesem roboczym**:

- **React + TypeScript** — interfejs importu, kolejki, kontroli i eksportu;
- **FastAPI** — lokalne API, zarządzanie zadaniami i statycznymi zasobami;
- **worker Pythona** — ekstrakcja PDF i eksport poza cyklem żądania HTTP;
- **SQLite w trybie WAL** — metadane, wyniki, problemy, historia zmian i kolejka;
- **lokalny system plików** — oryginalne PDF-y, renderowane strony i XLSX;
- **PyMuPDF + pdfplumber** — pozycjonowany tekst, geometria i rendering;
- **OpenPyXL** — tworzenie znormalizowanych skoroszytów.

System działa bez chmury, telemetrii i zewnętrznych modeli. MVP nie wykonuje OCR. Granice modułów pozwalają dodać OCR później bez przebudowy domeny i UI.

## 2. Kontekst i wymagania

### 2.1. Problem

Zwykłe kopiowanie tekstu z KPiR w PDF daje liniowy ciąg o błędnej kolejności, np. litery nagłówków są rozstrzelone, a wartości z wielu kolumn mieszają się w jednym wierszu. Sam tekst nie zawiera wystarczającej informacji o strukturze. Parser musi korzystać z pozycji słów i linii na stronie.

### 2.2. Ustalone wymagania funkcjonalne

1. Obsługa tekstowych PDF-ów KPiR generowanych przez programy księgowe.
2. Automatyczne rozpoznanie tabeli, rekordów i kolumn.
3. Znormalizowany wynik: jeden zapis KPiR na jeden wiersz.
4. Poprawne typy danych: data, tekst, kwota, pustka.
5. Tryb szybkiego eksportu oraz pełny tryb kontroli.
6. W trybie kontroli:
   - PDF i tabela są widoczne równocześnie;
   - kliknięcie komórki zaznacza źródło na stronie;
   - użytkownik może edytować komórki i wiersze;
   - można filtrować problemy i niską pewność.
7. Przed utworzeniem XLSX użytkownik może:
   - wybrać i uporządkować kolumny, np. pominąć opis zdarzenia lub pozostałe przychody;
   - ręcznie wskazać albo wykluczyć wiersze;
   - filtrować wiersze według wartości kwotowych, co najmniej „Wydatki razem” od określonej kwoty i/lub do określonej kwoty;
   - zobaczyć liczbę pasujących rekordów i podsumowanie konfiguracji.
8. Import wielu PDF-ów i obsługa paczek liczących tysiące stron.
9. Postęp, anulowanie, ponowienie i odzyskanie pracy po restarcie.
10. Eksport XLSX zgodny z konfiguracją oraz usunięcie wszystkich danych dokumentu.

### 2.3. Wymagania niefunkcjonalne

- 100% lokalnego przetwarzania i brak ruchu sieciowego na zewnątrz;
- Windows, macOS i Linux;
- uruchamianie skryptem deweloperskim;
- deterministyczność ekstrakcji;
- ograniczone zużycie pamięci niezależne od liczby stron całej paczki;
- dokładność i audytowalność ważniejsze od „zgadywania” brakujących danych;
- odporność na uszkodzony dokument i błąd pojedynczej strony;
- jedna lokalna instancja i jeden użytkownik; brak współpracy wieloosobowej w MVP.

### 2.4. Poza zakresem MVP

- OCR skanów;
- dokumenty inne niż KPiR;
- usługi AI/LLM i API chmurowe;
- porady podatkowe i automatyczne księgowanie;
- instalator desktopowy;
- serwer dostępny w sieci LAN;
- synchronizacja między komputerami.

## 3. Założenia i ryzyka wejścia

### Założenia

- PDF zawiera tekst z dostępnymi współrzędnymi glifów lub słów.
- Dokument ma powtarzalny nagłówek i logiczne kolumny KPiR.
- Strony mogą mieć różne rozmiary, rotację, fonty i drobne przesunięcia.
- Jeden PDF może obejmować wiele miesięcy i stron.
- W różnych programach księgowych wystąpią warianty tego samego formularza.

### Najważniejsze ryzyka

| Ryzyko | Skutek | Ograniczenie |
|---|---|---|
| PDF przechowuje tekst w nietypowej kolejności | błędne łączenie pól | użycie tokenów z geometrią zamiast liniowego tekstu |
| Wiersz zajmuje kilka linii | przesunięcie kolumn lub podział rekordu | hybrydowa segmentacja na podstawie linii, numeru LP i odstępów pionowych |
| Nagłówki/stopki wpadają do danych | fałszywe rekordy | profile stref wykluczonych i detekcja powtarzalnych elementów |
| Wariant układu nie pasuje do profilu | pozornie poprawne złe dane | minimalny wynik dopasowania profilu; w przeciwnym razie status „wymaga konfiguracji” |
| Kwota zostaje przypisana do sąsiedniej kolumny | błąd księgowy | geometria, walidacja sum, confidence i ręczna kontrola |
| Bardzo duża paczka wyczerpuje RAM | zatrzymanie aplikacji | stronicowe/chunkowe przetwarzanie i paginowane API |
| Złośliwy lub uszkodzony PDF | crash / zużycie zasobów | proces workera, limity, preflight, timeout i błąd izolowany do dokumentu/strony |
| Profil prawny zmienia się w czasie | zły schemat eksportu | wersjonowane profile niezależne od kodu UI |

## 4. Decyzja architektoniczna

### 4.1. Styl: modularny monolit + worker

Na komputerze użytkownika działają trzy procesy logiczne:

```text
┌──────────────────────┐       HTTP / SSE       ┌──────────────────────┐
│ React w przeglądarce │ ◄────────────────────► │ FastAPI (loopback)   │
└──────────────────────┘                         └──────────┬───────────┘
                                                          │ SQLite / pliki
                                               ┌──────────▼───────────┐
                                               │ Worker ekstrakcji     │
                                               │ i eksportu            │
                                               └──────────┬───────────┘
                                                          │
                              ┌───────────────────────────▼─────────────┐
                              │ katalog danych + SQLite WAL             │
                              └─────────────────────────────────────────┘
```

Proces API pozostaje responsywny. Worker pobiera trwałe zadania z SQLite. Dla CPU-intensywnych albo ryzykownych operacji PDF worker może uruchamiać krótkotrwałe procesy potomne z ograniczoną współbieżnością.

### 4.2. Dlaczego nie mikroserwisy i Celery

- jedna lokalna instancja nie uzasadnia brokera Redis/RabbitMQ;
- dodatkowe usługi komplikują start na trzech systemach;
- SQLite wystarcza jako trwała kolejka dla jednego hosta;
- granice modułów pozwolą później wydzielić usługi, jeśli skala faktycznie tego zażąda.

### 4.3. Dlaczego nie przetwarzanie synchroniczne

Tysiące stron mogą być analizowane przez wiele minut. Zamykanie żądania, odświeżenie przeglądarki lub błąd jednej strony nie może kasować pracy. Dlatego import tylko zapisuje dane i tworzy zadanie.

## 5. Komponenty

### 5.1. Frontend

**Technologie:** React, TypeScript, Vite, TanStack Query; dla dużej tabeli komponent z wirtualizacją. Biblioteka stanu globalnego tylko wtedy, gdy stan serwerowy i lokalny formularza nie wystarczą.

Funkcje:

- import plików i tworzenie paczek;
- lista zadań z postępem, ponawianiem i anulowaniem;
- widok dokumentów i problemów;
- wirtualizowana tabela rekordów;
- panel strony PDF jako lokalny obraz z warstwą SVG dla zaznaczeń;
- edycja komórek z walidacją;
- konfigurator eksportu: wybór/kolejność kolumn, ręczny wybór wierszy, filtry kwotowe i podgląd wyniku;
- eksport i usuwanie danych.

Nie renderujemy pliku przez zewnętrzny viewer ani CDN. Backend renderuje stronę do PNG/WebP lub dostarcza lokalny PDF.js spakowany z aplikacją. Dla MVP rekomendowany jest obraz strony + SVG, bo jest prostszy, deterministyczny i nie ujawnia pliku dodatkowym komponentom.

### 5.2. API FastAPI

Odpowiedzialności:

- streamingowy import plików z limitami;
- komendy tworzenia/anulowania/ponawiania zadań;
- paginowane odczyty rekordów i problemów;
- zapis ręcznych poprawek z kontrolą `revision`;
- obrazy stron i dane nakładek;
- walidacja konfiguracji eksportu i szybki podgląd liczby pasujących rekordów;
- przygotowanie/pobranie eksportu;
- SSE z postępem;
- polityki bezpieczeństwa dla lokalnego serwera.

API nie wykonuje ekstrakcji ani renderowania na żądanie, jeśli wynik nie znajduje się w cache’u. Brakujący render tworzy zadanie o wysokim priorytecie albo zostaje przygotowany podczas analizy dokumentu.

### 5.3. Worker

Odpowiedzialności:

- atomowe przejęcie następnego zadania z kolejki;
- heartbeat i wykrywanie zadań przerwanych;
- pipeline preflight, renderowania, ekstrakcji i eksportu;
- okresowe sprawdzanie anulowania;
- zapisywanie checkpointu po stronie lub małej grupie stron;
- raportowanie postępu z ograniczeniem częstotliwości zapisów;
- izolowanie błędów dokumentu i strony;
- sprzątanie plików tymczasowych.

Domyślna współbieżność powinna być zachowawcza: jeden dokument na worker i maksymalnie kilka procesów stron, wyliczonych z CPU/RAM. Użytkownik może ją zmienić w ustawieniach lokalnych.

### 5.4. Domena i profile KPiR

Domena zawiera:

- `Document`, `Page`, `ExtractionRun`;
- `KpirRecord`, `Cell`, `SourceSpan`;
- `Issue`, `Correction`, `Export`, `ExportDefinition`, `RowSelection`, `ExportFilter`;
- typy `Money`, `BusinessDate`, `Confidence`;
- reguły walidacji niezależne od API i bazy.

Profil KPiR jest wersjonowanym plikiem YAML/JSON, np. `kpir_pl_2018@1`. Zawiera co najmniej:

- identyfikator i wersję profilu;
- frazy nagłówka i minimalny próg dopasowania;
- definicje kolumn, typy i kolejność eksportu;
- względne kotwice/granice kolumn lub reguły ich wyznaczania;
- strefy nagłówka i stopki;
- mapowanie symboli oznaczających pustkę;
- reguły składania tekstu;
- walidacje i zależności sum;
- progi confidence;
- nazwy kolumn i formaty XLSX.

Profil nie powinien wykonywać dowolnego kodu. Konfiguracja jest walidowana schematem przy starcie.

### 5.5. Repozytorium danych

SQLite przechowuje relacyjne dane i małe artefakty diagnostyczne. System plików przechowuje duże pliki binarne.

Proponowany katalog:

```text
<data-dir>/
├── app.db
├── originals/<document-uuid>/source.pdf
├── pages/<document-uuid>/<page>.webp
├── exports/<export-uuid>/result.xlsx
├── temp/<job-uuid>/...
└── logs/application.log
```

`data-dir` pochodzi z platformowego katalogu danych użytkownika, nie z repozytorium. Nazwa przesłanego pliku występuje wyłącznie jako oczyszczona metadana wyświetlana w UI.

## 6. Model danych

Poniższy model jest logiczny; szczegóły typów określi migracja.

### 6.1. Tabele główne

#### `batches`

- `id`, `display_name`;
- `created_at`, `updated_at`;
- agregaty postępu opcjonalnie cache’owane.

#### `documents`

- `id`, `batch_id`, `original_name`, `stored_path`;
- `sha256`, `size_bytes`, `page_count`;
- `status`, `detected_profile_id`, `declared_period_from/to`;
- `created_at`, `deleted_at`.

Hash nie służy do globalnej deduplikacji bez zgody użytkownika; może ostrzegać o ponownym imporcie.

#### `pages`

- `id`, `document_id`, `page_number`;
- `width`, `height`, `rotation`;
- `text_available`, `status`, `render_path`;
- `error_code`, `error_summary`.

#### `extraction_runs`

- `id`, `document_id`;
- `extractor_version`, `profile_id`, `profile_version`;
- `config_hash`, `status`, `started_at`, `finished_at`.

Nowa wersja algorytmu tworzy nowy run; nie nadpisuje bezpowrotnie poprzedniego.

#### `records`

- `id`, `extraction_run_id`, `logical_index`;
- `source_page_from/to`, `status`, `confidence`;
- `revision`, `created_at`, `updated_at`.

#### `cells`

- `id`, `record_id`, `column_key`;
- `raw_text`, `normalized_text`, `parsed_value_text`, `value_type`;
- `confidence`, `is_manual`, `revision`;
- `source_page`, `bbox_x0/y0/x1/y1`;
- `extraction_meta_json` dla ograniczonej diagnostyki.

Wartość efektywna to ręczna korekta, jeśli istnieje, w przeciwnym razie wynik ekstrakcji. Implementacja może rozdzielić `extracted_cells` i `cell_corrections`; jest to preferowane, jeśli uprości zachowanie pełnej historii.

#### `issues`

- `id`, `document_id`, opcjonalnie `page_id`, `record_id`, `cell_id`;
- `code`, `severity`, `message_key`, `details_json`;
- `status` (`open`, `acknowledged`, `resolved`);
- `created_at`, `resolved_at`.

#### `corrections`

- `id`, `cell_id`, `old_value_text`, `new_value_text`;
- `old_value_type`, `new_value_type`;
- `reason`, `actor`, `created_at`, `base_revision`.

#### `jobs`

- `id`, `type`, `priority`, `status`;
- `batch_id`, `document_id`, `payload_json`;
- `progress_current`, `progress_total`, `progress_phase`;
- `cancel_requested`, `attempt`, `max_attempts`;
- `lease_owner`, `lease_expires_at`, `heartbeat_at`;
- `error_code`, `error_summary`;
- `created_at`, `started_at`, `finished_at`.

#### `exports`

- `id`, `batch_id` lub `document_id`, `job_id`;
- `format`, `policy`, `path`, `sha256`, `size_bytes`;
- `definition_json` — niezmienny snapshot zakresu, kolumn w zadanej kolejności, filtrów i sposobu wyboru wierszy;
- `source_revision` lub identyfikatory runów użytych do zbudowania wyniku;
- `result_row_count`, `result_column_count`;
- `extractor/profile version`, `created_at`.

#### `export_presets` (opcjonalne w MVP)

- `id`, `name`, `profile_id`, `definition_json`;
- `created_at`, `updated_at`.

Preset ułatwia ponowne użycie wyboru, np. „bez opisu i pozostałych przychodów”, ale gotowy eksport zawsze przechowuje własny snapshot. Zmiana presetu nie może zmienić już utworzonego pliku ani jego metadanych.

### 6.2. Stany zadania

```text
QUEUED → RUNNING → SUCCEEDED
   │        │  └──→ CANCELLING → CANCELLED
   │        └─────→ FAILED → QUEUED (retry)
   └──────────────→ CANCELLED
RUNNING --restart/expired lease--> INTERRUPTED → QUEUED lub FAILED
```

Zmiana stanu odbywa się w krótkiej transakcji. Worker przejmuje zadanie przez warunkowy `UPDATE`. Lease i heartbeat zapobiegają pozostawieniu zadania w `RUNNING` po awarii.

### 6.3. Retencja

Domyślnie dane pozostają lokalnie do ręcznego usunięcia. Usunięcie dokumentu jest operacją kontrolowaną:

1. oznaczenie jako `deleting`;
2. zablokowanie i anulowanie zadań;
3. usunięcie eksportów, renderów, plików tymczasowych i PDF-u;
4. usunięcie/zanonimizowanie danych relacyjnych w transakcji;
5. status końcowy lub raport artefaktów, których nie udało się usunąć.

## 7. Pipeline ekstrakcji

### 7.1. Import i preflight

1. API zapisuje upload strumieniowo do pliku tymczasowego, jednocześnie licząc SHA-256 i rozmiar.
2. Sprawdza sygnaturę `%PDF-`, limit rozmiaru i bezpieczną nazwę metadanej.
3. Atomowo przenosi plik do katalogu oryginałów.
4. Tworzy `document` oraz zadanie `ANALYZE_DOCUMENT`.
5. Worker sprawdza:
   - możliwość otwarcia PDF;
   - szyfrowanie/hasło;
   - liczbę stron i limity;
   - rotację i geometrię;
   - obecność tekstu na reprezentatywnych stronach.
6. PDF bez użytecznej warstwy tekstowej otrzymuje kod `OCR_REQUIRED`, a nie pusty, „udany” eksport.

### 7.2. Kanoniczna geometria strony

Biblioteki PDF mogą zwracać współrzędne w różnych układach. Adapter infrastruktury przelicza tokeny do kanonicznego układu po uwzględnieniu rotacji. Do bazy zapisuje prostokąty znormalizowane `0..1`.

Podstawowa struktura tokenu:

```text
Token {
  raw_text,
  bbox_norm,
  baseline,
  font_name?, font_size?,
  block_id?, line_id?, word_id?
}
```

Surowe tokeny są niemutowalne w obrębie runu.

### 7.3. Rozpoznanie profilu

Dla kilku reprezentatywnych stron system oblicza wynik dopasowania:

- obecność fraz „KSIĘGA PRZYCHODÓW I ROZCHODÓW”, „Nr dowodu”, „Opis zdarzenia” itp.;
- kolejność kotwic w osi X;
- podobieństwo geometrii nagłówka;
- obecność numerów kolumn;
- spójność na kolejnych stronach.

Profil zostaje wybrany tylko ponad minimalnym progiem i przy odpowiednim marginesie nad kolejnym kandydatem. Niejednoznaczność generuje `PROFILE_AMBIGUOUS` i wymaga wyboru użytkownika. Użytkownik może jawnie wskazać profil przed przetwarzaniem.

### 7.4. Granice tabeli i kolumn

Podejście hybrydowe:

1. odczyt obiektów rysunkowych (linie/prostokąty), jeśli istnieją;
2. wykrycie kotwic tekstowych nagłówka;
3. dopasowanie relatywnego wzorca profilu do szerokości strony;
4. oszacowanie granic kolumn;
5. ocena jakości na podstawie liczby tokenów przecinających granice i typów danych.

Każda granica ma confidence. Niska jakość geometrii oznacza ostrzeżenie strony. Parser nie powinien wymuszać danych na profil, jeśli dopasowanie jest słabe.

### 7.5. Segmentacja rekordów

Sygnały, w kolejności ważności:

- poziome linie tabeli;
- token LP w pierwszej kolumnie;
- data i numer dokumentu w oczekiwanych kolumnach;
- klastry linii bazowych i przerwy pionowe;
- kontynuacja tekstu w kolumnach opisowych;
- granice nagłówka i stopki.

Wiersz może zajmować kilka linii. Segmentator najpierw tworzy pasy rekordów, a dopiero potem przypisuje tokeny do kolumn. Kontynuacja rekordu między stronami musi być jawnie oznaczona i połączona tylko przy wystarczających przesłankach.

### 7.6. Przypisanie i składanie tekstu

Token trafia do komórki na podstawie nakładania prostokąta z kolumną i pasem wiersza. Dla tokenu przecinającego granicę stosuje się kolejno:

1. największy procent powierzchni;
2. pozycję środka;
3. oczekiwany typ kolumny;
4. w razie niejednoznaczności — problem, bez agresywnego zgadywania.

Tekst jest składany według linii i współrzędnej X. Normalizator może:

- ujednolicić białe znaki;
- rozpoznać znaki używane przez raport jako puste pole;
- naprawić rozstrzelone litery na podstawie odległości między glifami;
- złożyć wyrazy podzielone zmianą linii.

Każda transformacja zapisuje kod operacji, a `raw_text` pozostaje dostępny.

### 7.7. Parsowanie typów

#### Kwoty

- akceptuj polski przecinek i kropkę jako separator dziesiętny zgodnie z jednoznacznymi regułami;
- spacje zwykłe i nierozdzielające mogą być separatorami tysięcy;
- wynik: `Decimal` ze skalą do 2;
- pusty symbol to `null`, nie `0.00`;
- niejednoznaczny format tworzy problem `INVALID_MONEY`.

#### Daty

- oczekiwany format profilu może obejmować `DD.MM.RR` i `DD.MM.RRRR`;
- dwucyfrowy rok wymaga kontekstu okresu dokumentu lub jawnej reguły profilu;
- wynik to pełna data ISO;
- data niemożliwa lub poza okresem generuje problem.

#### Tekst

- zachowaj polskie znaki;
- nie zmieniaj samoczynnie nazw kontrahentów na podstawie słownika;
- normalizacja ma być minimalna i odwracalna.

### 7.8. Confidence i diagnostyka

Confidence nie jest „prawdopodobieństwem księgowej poprawności”, lecz powtarzalnym wynikiem jakości ekstrakcji. Proponowane składowe:

- dopasowanie profilu;
- pewność granic tabeli, wiersza i kolumny;
- odległość tokenów od granic;
- powodzenie parsera typu;
- zgodność reguł sum i okresu;
- kompletność pól wymaganych;
- liczba zastosowanych niepewnych transformacji.

Profil definiuje wagi i progi:

- `high`: bez oznaczenia;
- `review`: widoczne ostrzeżenie;
- `critical`: blokada szybkiego eksportu według polityki.

Zapisujemy wynik całkowity oraz kody składowych, aby dało się wyjaśnić, dlaczego pole wymaga kontroli.

### 7.9. Walidacja

Walidacja działa po ekstrakcji oraz po każdej korekcie użytkownika.

Przykładowe kody:

- `REQUIRED_VALUE_MISSING`;
- `INVALID_DATE`, `DATE_OUTSIDE_PERIOD`;
- `INVALID_MONEY`, `MONEY_SCALE_EXCEEDED`;
- `INCOME_TOTAL_MISMATCH`;
- `EXPENSE_TOTAL_MISMATCH`;
- `ROW_NUMBER_GAP`;
- `POSSIBLE_DUPLICATE`;
- `TOKEN_CROSSES_COLUMN`;
- `LOW_PROFILE_CONFIDENCE`;
- `POSSIBLE_ROW_CONTINUATION`;
- `OCR_REQUIRED`.

Reguła sum porównuje wartości z PDF-u. Nie zastępuje sumy wartością obliczoną. Problem znika po korekcie, ale historia problemu i zmiany pozostaje audytowalna.

## 8. Schemat KPiR i profile

Przykład wskazuje formularz KPiR z numerowanymi kolumnami i zagnieżdżonym nagłówkiem. Nie należy na podstawie jednego tekstowego wklejenia ustalać ostatecznych współrzędnych ani pełnej semantyki każdej kolumny. Przed implementacją profilu `kpir_pl_2018` potrzebne są 1–3 zanonimizowane PDF-y źródłowe i ręcznie przygotowany oczekiwany XLSX/CSV.

Docelowy profil powinien nadawać każdej kolumnie stabilny klucz, np.:

```text
row_number
business_date
evidence_number
contractor_name
contractor_address
event_description
income_goods_services
other_income
total_income
goods_materials_purchase
purchase_incidental_costs
remuneration
other_expenses
total_expenses
research_development_costs
research_development_description
notes
```

Lista jest robocza. Musi zostać zweryfikowana z faktycznym formularzem i oczekiwanym wynikiem. Nagłówek dokumentu, numer kolumny i etykieta profilu powinny być zapisane osobno, aby różne wersje historyczne mogły mapować się na stabilny model domenowy.

## 9. Ręczna kontrola

### 9.1. Układ ekranu

```text
┌────────────────────────────┬──────────────────────────────────────┐
│ PDF / strona / zoom        │ Tabela KPiR (wirtualizowana)         │
│                            │ filtry: problemy, confidence, strona │
│ [podświetlone źródło]      │ [edytowana komórka]                 │
├────────────────────────────┴──────────────────────────────────────┤
│ Szczegóły: raw, normalized, parsed, confidence, problemy, historia│
└───────────────────────────────────────────────────────────────────┘
```

### 9.2. Zachowanie

- zaznaczenie komórki ładuje właściwą stronę i rysuje prostokąt;
- zaznaczenie problemu ustawia fokus na powiązanej komórce/wierszu;
- edycja zachowuje surową wartość ekstrakcji i tworzy `correction`;
- UI pokazuje oznaczenie wartości ręcznej;
- użytkownik może cofnąć korektę do wyniku ekstrakcji;
- ponowne przetwarzanie tworzy nowy run, a migracja ręcznych korekt wymaga jawnego dopasowania rekordu i potwierdzenia;
- konflikt `revision` zwraca 409 i pokazuje obie wartości;
- skróty klawiaturowe obsługują przejście do następnego problemu, zapis i nawigację po komórkach.

### 9.3. Wydajność UI

- serwerowa paginacja rekordów i problemów;
- wirtualizacja wierszy i kolumn;
- pobieranie obrazu tylko bieżącej strony oraz prefetch sąsiednich;
- debounce zapisu edycji nie może ryzykować utraty danych: aktywna edycja ma jawny stan zapisu;
- zbiorcze operacje są osobnymi komendami z podglądem liczby zmian.

### 9.4. Konfigurator eksportu

Przed utworzeniem pliku użytkownik przechodzi przez konfigurator z czterema częściami:

1. **Zakres źródłowy** — dokument, wybrane dokumenty albo cała paczka.
2. **Kolumny** — lista kolumn profilu z checkboxami i możliwością zmiany kolejności. Co najmniej jedna kolumna musi pozostać wybrana.
3. **Wiersze i filtry**:
   - wszystkie pasujące wiersze;
   - tylko ręcznie zaznaczone wiersze;
   - wszystkie pasujące oprócz ręcznie wykluczonych;
   - filtr kwotowy z wyborem kolumny, minimum `od` i maksimum `do`.
4. **Podsumowanie** — liczba wynikowych wierszy i kolumn, aktywne filtry, liczba otwartych problemów oraz przykładowe pierwsze rekordy.

Minimalny filtr MVP to zakres dla `total_expenses` („Wydatki razem”). Model filtra powinien być jednak ogólny dla typowanych kolumn kwotowych, aby później bez zmiany kontraktu można było filtrować np. po przychodzie lub wynagrodzeniach.

Ważne zasady UX i domeny:

- kolumna używana do filtrowania nie musi należeć do eksportowanych kolumn;
- filtry i wybór działają na **wartościach efektywnych**, czyli uwzględniają ręczne korekty;
- minimum i maksimum są domyślnie włączne: `value >= min` i `value <= max`;
- można podać tylko jedną granicę;
- rekord z pustą lub niepoprawną kwotą nie spełnia aktywnego filtra kwotowego, a podgląd pokazuje liczbę takich odrzuceń;
- `min > max` jest błędem walidacji i blokuje eksport;
- najpierw obliczany jest zbiór spełniający filtry, a potem stosowany wybór ręczny: `EXPLICIT` tworzy przecięcie z wybranymi ID, natomiast `ALL_MATCHING` odejmuje wykluczone ID;
- aby wyeksportować ręcznie wskazany wiersz niespełniający filtra, użytkownik musi usunąć lub zmienić filtr; UI powinien to jasno komunikować;
- filtry nigdy nie usuwają rekordów z bazy — tworzą wyłącznie projekcję eksportową;
- wynik zachowuje kolejność źródłową, chyba że w przyszłości użytkownik jawnie wybierze sortowanie;
- przy zmianie danych po wyświetleniu podglądu UI oznacza go jako nieaktualny i wymaga odświeżenia przed startem eksportu;
- konfigurację można opcjonalnie zapisać jako nazwany preset.

Dla dużych paczek UI nie przesyła tysięcy identyfikatorów w trybie „zaznacz wszystko”. `RowSelection` ma dwie skalowalne reprezentacje:

```text
EXPLICIT: included_record_ids
ALL_MATCHING: filter_expression + excluded_record_ids
```

Ręczne zaznaczenie pojedynczych wierszy używa `EXPLICIT`. „Wszystkie pasujące” używa `ALL_MATCHING`, a odznaczone wyjątki trafiają do `excluded_record_ids`.

## 10. Eksport XLSX

### 10.1. Zawartość

Minimalny skoroszyt:

- arkusz `KPiR` — wybrane rekordy w kolejności źródłowej;
- wyłącznie kolumny zaznaczone przez użytkownika, w wybranej przez niego kolejności;
- polskie nazwy kolumn pochodzące z profilu;
- komórki dat jako typ Excel Date;
- kwoty jako wartości numeryczne z formatem `0.00`;
- puste wartości jako puste komórki, nie zero;
- zamrożony nagłówek, filtr i rozsądne szerokości kolumn.

Opcjonalny arkusz `Problemy` może być włączony polityką eksportu, ale nie powinien mieszać się z głównymi danymi. Użytkownik wybiera zakres: dokument, paczka lub wybrane dokumenty.

Eksporter wykonuje pipeline:

```text
zakres dokumentów → wartości efektywne → filtry → wybór/wykluczenia wierszy
→ projekcja i kolejność kolumn → zapis XLSX
```

Snapshot `ExportDefinition` jest utrwalany przed uruchomieniem workera. Worker nie może ponownie interpretować bieżącego stanu formularza w przeglądarce. Jeśli dane źródłowe zmieniły się między podglądem a startem, API zwraca konflikt lub wymaga jawnego odświeżenia podglądu.

### 10.2. Definicja i semantyka filtrów

Przykładowa definicja eksportu:

```json
{
  "scope": {"type": "documents", "documentIds": ["uuid-1"]},
  "columnKeys": [
    "business_date",
    "evidence_number",
    "contractor_name",
    "total_expenses"
  ],
  "rowSelection": {
    "mode": "ALL_MATCHING",
    "excludedRecordIds": ["record-12"]
  },
  "filters": [
    {
      "type": "money_range",
      "columnKey": "total_expenses",
      "min": "100.00",
      "max": "1000.00",
      "bounds": "inclusive"
    }
  ],
  "policy": "strict",
  "includeIssuesSheet": false,
  "sourceRevision": "opaque-revision-token"
}
```

Kwoty w kontrakcie są tekstem dziesiętnym. API waliduje, czy `columnKey` istnieje w profilu i ma zgodny typ. Filtry łączymy operatorem AND; jeżeli w przyszłości potrzebne będą grupy OR, wymagają wersjonowanego rozszerzenia modelu, a nie niejawnej zmiany semantyki. Kolejność `columnKeys` jest kolejnością kolumn w XLSX.

### 10.3. Polityki

- `strict` — blokuje eksport przy otwartych problemach krytycznych w wybranych wierszach;
- `reviewed` — pozwala po jawnym potwierdzeniu problemów;
- `draft` — eksport roboczy z wyraźną informacją w metadanych/arkuszu problemów.

Problemy w rekordach odrzuconych przez filtr nie blokują eksportu, ale podgląd może podać ich liczbę osobno. Tryb szybki domyślnie używa `strict`. Eksport jest zadaniem asynchronicznym i zapisuje wersje ekstraktora/profilu oraz hash pliku wynikowego.

### 10.4. Ochrona przed formułami

Teksty zaczynające się od `=`, `+`, `-` lub `@` mogą zostać zinterpretowane przez Excel jako formuły. W kolumnach tekstowych eksport musi wymusić typ tekstowy/bezpieczne kodowanie. Kwoty i daty są przekazywane jako typowane wartości, nie tekst użytkownika.

## 11. API v1

Kontrakt należy opisać w OpenAPI. Roboczy zestaw endpointów:

### Import i zadania

```text
POST   /api/v1/batches
POST   /api/v1/batches/{batchId}/documents       multipart, jeden lub wiele plików
GET    /api/v1/batches/{batchId}
GET    /api/v1/jobs?batchId=&status=&cursor=
GET    /api/v1/jobs/{jobId}
POST   /api/v1/jobs/{jobId}/cancel
POST   /api/v1/jobs/{jobId}/retry
GET    /api/v1/events                            SSE
```

### Dokument i kontrola

```text
GET    /api/v1/documents/{documentId}
GET    /api/v1/documents/{documentId}/pages/{pageNumber}/image
GET    /api/v1/documents/{documentId}/records?cursor=&limit=&issue=&confidence=
GET    /api/v1/records/{recordId}
PATCH  /api/v1/cells/{cellId}                    body: value, valueType, baseRevision, reason?
DELETE /api/v1/cells/{cellId}/correction          body: baseRevision
GET    /api/v1/documents/{documentId}/issues?cursor=&severity=&status=
POST   /api/v1/issues/{issueId}/acknowledge
DELETE /api/v1/documents/{documentId}
```

### Eksport

```text
POST   /api/v1/export-previews                   ExportDefinition bez tworzenia pliku
POST   /api/v1/exports                           zatwierdzony ExportDefinition + revision podglądu
GET    /api/v1/exports/{exportId}
GET    /api/v1/exports/{exportId}/file
DELETE /api/v1/exports/{exportId}
GET    /api/v1/export-presets
POST   /api/v1/export-presets                    opcjonalnie: zapis konfiguracji bez wyboru rekordów
DELETE /api/v1/export-presets/{presetId}
```

Odpowiedź podglądu zawiera co najmniej `matchingRowCount`, `columnCount`, `excludedForNullOrInvalidCount`, liczbę problemów według poziomu, próbkę pierwszych rekordów i nieprzezroczysty `sourceRevision`. Utworzenie eksportu z nieaktualnym `sourceRevision` zwraca HTTP 409, aby plik nie różnił się po cichu od podglądu.

### Zasady kontraktu

- błędy mają stabilne `code`, komunikat użytkownika i `correlationId`;
- wartości pieniężne w JSON, także granice filtrów, są stringami;
- klucze kolumn pochodzą z aktywnego profilu i są walidowane po stronie serwera;
- `ETag` lub `revision` chroni edycję, a `sourceRevision` wiąże eksport z podglądem;
- paginacja kursorowa jest preferowana dla dużych danych;
- upload i download są strumieniowe;
- żaden endpoint listy nie zwraca wszystkich rekordów dużej paczki naraz;
- serwer ponownie stosuje i waliduje filtry; nigdy nie ufa tylko temu, co ukrył frontend.

## 12. Kolejka i przetwarzanie dużych paczek

### 12.1. Typy zadań

- `ANALYZE_DOCUMENT`;
- `EXTRACT_DOCUMENT` lub `EXTRACT_PAGE_CHUNK`;
- `VALIDATE_DOCUMENT`;
- `RENDER_PAGE`;
- `EXPORT_XLSX`;
- `DELETE_DOCUMENT_ARTIFACTS`;
- `REPROCESS_DOCUMENT`.

Na początku można połączyć analizę, ekstrakcję i walidację w jedno zadanie dokumentu, o ile istnieje checkpoint per strona. Drobniejsze zadania wprowadzamy dopiero po pomiarach.

### 12.2. Checkpointy

Po każdej stronie lub małym chunku worker zapisuje:

- status stron;
- wyodrębnione tokeny/komórki potrzebne do wznowienia;
- problemy;
- postęp;
- ostatni trwały checkpoint.

Transakcja nie może obejmować całego dokumentu. Po restarcie przetwarzanie zaczyna się od ostatniego kompletnego checkpointu, a nie od połowy zapisu strony.

### 12.3. Backpressure i zasoby

- globalny limit aktywnych dokumentów;
- limit renderów wykonywanych równolegle;
- priorytet dla stron otwieranych w UI i małych eksportów;
- okresowe usuwanie plików tymczasowych osieroconych przez przerwane zadania;
- brak przechowywania obrazów wszystkich stron w RAM;
- indeksy SQLite dla statusów zadań, dokumentu, runu i cursor pagination.

## 13. Bezpieczeństwo i prywatność

### 13.1. Model zagrożeń

Mimo działania lokalnego zagrożeniem są:

- złośliwy PDF;
- strona internetowa próbująca połączyć się z usługą na localhost;
- path traversal przez nazwę pliku;
- formuła w wyeksportowanym XLSX;
- wyciek danych do logów, crash reportów lub telemetrii;
- przypadkowe wystawienie serwera w LAN.

### 13.2. Kontrole

- bind `127.0.0.1` domyślnie;
- losowy wolny port albo konfigurowalny port lokalny;
- ścisła lista dozwolonych nagłówków `Host` i `Origin`;
- CORS wyłączony w produkcyjnym, same-origin buildzie; w dev tylko jawne originy Vite;
- CSRF token dla operacji zmieniających stan;
- CSP co najmniej `default-src 'self'; connect-src 'self'; img-src 'self' blob: data:`;
- brak zewnętrznych fontów, CDN i analityki;
- limity uploadu, stron, czasu oraz zasobów parsera;
- zależności z lockfile’ów i skan podatności możliwy w środowisku deweloperskim, bez telemetrii runtime;
- logowanie kodów i identyfikatorów, nie treści dokumentu;
- sanitizacja komórek tekstowych przy eksporcie;
- bezpieczne nagłówki pobierania i niezgadywalne identyfikatory plików;
- sprzątanie po usunięciu i przy starcie aplikacji.

### 13.3. Offline jako testowalny wymóg

Test end-to-end powinien działać z zablokowaną siecią zewnętrzną. Build produkcyjny nie może zawierać URL-i telemetrycznych ani importów z CDN. Pobranie zależności przy pierwszej konfiguracji deweloperskiej jest oddzielnym etapem i nie jest działaniem aplikacji.

## 14. Uruchamianie lokalne

### 14.1. Tryb developerski

Docelowo:

```text
python scripts/dev.py
```

Skrypt:

1. sprawdza wspierane wersje Pythona i Node;
2. sprawdza obecność zależności, ale nie instaluje ich bez zgody;
3. uruchamia migracje;
4. uruchamia API, worker i Vite jako procesy potomne;
5. czeka na gotowość;
6. otwiera przeglądarkę;
7. przekazuje sygnał zakończenia i sprząta procesy.

Konfiguracja z `.env` nie może zawierać sekretów produkcyjnych. Ważniejsze ustawienia:

```text
KPIR_DATA_DIR
KPIR_API_PORT
KPIR_FRONTEND_PORT
KPIR_MAX_UPLOAD_MB
KPIR_MAX_PAGES_PER_DOCUMENT
KPIR_WORKER_CONCURRENCY
KPIR_LOG_LEVEL
```

### 14.2. Przyszły tryb dystrybucyjny

W przyszłości frontend można zbudować do statycznych plików serwowanych przez FastAPI, a backend i worker spakować jako aplikację. Nie należy projektować domeny zależnie od Electron/Tauri/PyInstaller. Opakowanie jest adapterem startowym.

## 15. Obserwowalność bez wycieku danych

Lokalne logi strukturalne zawierają:

- czas, poziom, komponent;
- `correlation_id`, `job_id`, `document_id`;
- etap, czas trwania, liczby stron/rekordów/problemów;
- kod błędu i bezpieczny opis.

Nie zawierają tekstu komórek, nazw kontrahentów, numerów dokumentów ani pełnych ścieżek. Logi rotują według rozmiaru. UI udostępnia „pakiet diagnostyczny” tylko po świadomej akcji użytkownika; domyślnie pakiet ma konfigurację, wersje i zanonimizowane metryki, bez PDF-u i danych komórek.

Metryki lokalne potrzebne do strojenia:

- czas na stronę dla etapów;
- szczytowe użycie pamięci;
- liczba problemów według kodu;
- procent komórek ręcznie poprawionych;
- dokładność na korpusie golden, nie na danych użytkownika wysyłanych gdziekolwiek.

## 16. Strategia testów

### 16.1. Korpus testowy

Potrzebne są:

1. 1–3 zanonimizowane rzeczywiste PDF-y z przykładowego programu;
2. ręcznie zatwierdzony plik oczekiwanych rekordów;
3. strony z wieloliniowym opisem, pustymi i zerowymi kwotami;
4. strony z powtarzalnym nagłówkiem i stopką;
5. warianty rozmiaru strony/rotacji;
6. celowo uszkodzony i zaszyfrowany PDF;
7. syntetyczna duża paczka do testów wydajności.

PDF-y testowe nie mogą zawierać prawdziwych danych osobowych. Sama zamiana widocznego tekstu może nie wystarczyć: trzeba też usunąć metadane, załączniki i ukryte warstwy.

### 16.2. Piramida testów

- **unit:** geometria, `Decimal`, daty, profile, walidacje, confidence;
- **golden:** tokeny → oczekiwane rekordy i problemy;
- **integration:** rzeczywisty adapter PDF, SQLite i OpenPyXL;
- **export domain:** projekcja i kolejność kolumn, tryby wyboru wierszy, `min`/`max` osobno i razem, włączne granice, zero, pustka, niepoprawna kwota, `min > max`, wartość po korekcie oraz filtr po niewyeksportowanej kolumnie;
- **contract:** OpenAPI ↔ TypeScript, w tym `ExportDefinition` i konflikt `sourceRevision`;
- **frontend component:** tabela, viewer, edycja, konfigurator eksportu, nieaktualny podgląd i dostępność;
- **E2E:** import → kontrola → korekta → wybór kolumn/wierszy → podgląd → filtrowany eksport → usunięcie;
- **resilience:** kill workera, restart, anulowanie, brak miejsca, uszkodzona strona;
- **performance:** tysiące stron przy ograniczonej pamięci oraz `ALL_MATCHING` z wyjątkami bez materializacji wszystkich identyfikatorów;
- **security:** upload, traversal, Host/Origin, CSRF, formuły XLSX, brak ruchu zewnętrznego.

### 16.3. Miary jakości ekstrakcji

Na korpusie golden raportujemy osobno:

- trafność wykrycia rekordów;
- exact match tekstu po normalizacji;
- trafność kwot i dat;
- przypisanie wartości do właściwej kolumny;
- precision/recall flag `review` i `critical`;
- procent rekordów wymagających ręcznej poprawki.

Jedna łączna metryka może ukrywać krytyczne przesunięcie kwoty między kolumnami, dlatego nie wystarcza.

## 17. Plan implementacji

### Etap 0 — próbki i kontrakt danych

- pozyskać zanonimizowane PDF-y oraz oczekiwany XLSX/CSV;
- potwierdzić semantykę i kolejność kolumn profilu 2018;
- sprawdzić warstwę tekstową i obiekty linii w PyMuPDF/pdfplumber;
- stworzyć pierwszy golden fixture.

**Warunek wyjścia:** na jednej stronie potrafimy wyświetlić tokeny i prostokąty oraz mamy ręcznie zatwierdzony expected result.

### Etap 1 — pionowy prototyp parsera

- adapter PDF i kanoniczna geometria;
- profil `kpir_pl_2018@1`;
- wykrycie tabeli, wierszy i kolumn;
- parsowanie kwot/dat oraz walidacja;
- CLI: PDF → JSON/CSV diagnostyczny.

**Warunek wyjścia:** powtarzalny wynik na korpusie golden i raport błędów per komórka.

### Etap 2 — trwały backend

- FastAPI, migracje SQLite, magazyn plików;
- import, dokumenty, rekordy, problemy;
- trwała kolejka, worker, progress, cancel/retry/recovery;
- domena `ExportDefinition`, podgląd, filtry kwotowe i konfigurowalny eksport XLSX.

**Warunek wyjścia:** pełny przepływ przez API, wybór kolumn i filtrowanie wierszy oraz restart workera bez utraty ukończonych stron.

### Etap 3 — interfejs kontroli

- import i lista zadań;
- PDF + SVG overlay;
- wirtualizowana tabela;
- edycja z historią i optimistic concurrency;
- filtrowanie problemów;
- konfigurator eksportu z wyborem kolumn, wierszy, zakresem kwot i podglądem;
- eksport i usuwanie.

**Warunek wyjścia:** użytkownik poprawia niepewną komórkę, wybiera np. eksport bez opisu zdarzenia i pozostałych przychodów, ogranicza wiersze zakresem wydatków, sprawdza podgląd i pobiera zgodny XLSX.

### Etap 4 — paczki, wydajność i utwardzenie

- batch processing i priorytety;
- checkpointy oraz testy tysięcy stron;
- limity zasobów i izolacja parsera;
- test offline, bezpieczeństwo localhost i XLSX;
- dokumentacja startu i diagnostyki.

**Warunek wyjścia:** duża paczka nie powoduje liniowego wzrostu RAM, można ją anulować/wznowić, a błąd jednego PDF-u nie blokuje reszty.

### Etap 5 — kolejne warianty KPiR

- narzędzia do tworzenia profilu;
- wybór profilu w UI;
- porównanie runów i kontrolowana migracja korekt;
- dopiero po danych: decyzja o OCR.

## 18. ADR-y do utworzenia przy implementacji

- `ADR-001 modular-monolith-and-sqlite-queue.md`;
- `ADR-002 canonical-page-coordinate-system.md`;
- `ADR-003 versioned-kpir-profiles.md`;
- `ADR-004 immutable-extraction-and-corrections.md`;
- `ADR-005-decimal-and-xlsx-value-policy.md`;
- `ADR-006-localhost-security-model.md`;
- `ADR-007-pdf-library-benchmark.md` po sprawdzeniu próbek.

ADR jest wymagany, gdy zmiana:

- zmienia granice procesu lub magazynu danych;
- wprowadza usługę zewnętrzną;
- zmienia model precyzji, audytu lub prywatności;
- zmienia format profilu albo politykę eksportu;
- wprowadza niedeterministyczny model ML/AI.

## 19. Otwarte decyzje wymagające próbek

Poniższych kwestii nie należy zgadywać na podstawie wklejonego tekstu:

1. dokładna geometria i warianty nagłówka profilu KPiR 2018;
2. ostateczna lista i semantyka kolumn eksportu;
3. czy linie tabeli są obiektami wektorowymi, czy tylko znakami tekstowymi;
4. sposób zapisu pustych pól (`·`, kropka, brak tokenu lub inny glif);
5. zasady rekordów wieloliniowych i ewentualnej kontynuacji między stronami;
6. realne limity wielkości pliku i docelowy budżet czasu;
7. czy osobny arkusz problemów ma być domyślnie włączony.

Do zamknięcia tych decyzji potrzebne są zanonimizowane przykłady PDF i oczekiwany wynik. Architektura jest przygotowana tak, aby odpowiedzi zmieniały profil i konfigurację, a nie podstawowe granice systemu.

## 20. Kryteria akceptacji architektury

Architektura spełnia wymagania, jeśli implementacja na jej podstawie:

- działa bez internetu;
- zachowuje oryginalny PDF i pełne pochodzenie wartości;
- nie używa `float` dla pieniędzy;
- nie ukrywa rozbieżności walidacyjnych;
- obsługuje długie zadania poza HTTP, z postępem i anulowaniem;
- wznawia pracę po restarcie;
- skaluje się stronicowo, bez wczytywania całej paczki do RAM;
- umożliwia pełną korektę z podglądem źródła;
- pozwala przed eksportem wybrać i uporządkować kolumny oraz wybrać/wykluczyć wiersze;
- filtruje po dokładnych wartościach kwotowych `od`/`do`, uwzględniając ręczne korekty;
- pokazuje podgląd konfiguracji i liczby pasujących rekordów;
- eksportuje typowany, znormalizowany XLSX zgodny ze snapshotem wyboru;
- izoluje profile KPiR od UI i infrastruktury;
- może w przyszłości otrzymać adapter OCR bez naruszenia modelu domenowego.
