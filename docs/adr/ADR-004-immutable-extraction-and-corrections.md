# ADR-004: Niezmienna ekstrakcja i osobna warstwa korekt

**Status:** zaakceptowany · **Data:** 2026-08-17

## Kontekst
Dane księgowe muszą być audytowalne: trzeba wiedzieć, co odczytał parser, co
zmienił człowiek, kiedy i dlaczego.

## Decyzja
Tabela `cells` przechowuje niezmienny wynik ekstrakcji w obrębie runu. Ręczna
zmiana trafia do `cell_corrections` (aktualna wartość) i `corrections_history`
(pełny, tylko dopisywany ślad). *Wartość efektywna* to korekta, jeśli istnieje,
w przeciwnym razie ekstrakcja — rozstrzygane raz, w SQL, więc odczyty, filtry
i eksport nie mogą się rozjechać. Zapis chroni optimistic concurrency przez
`revision`; konflikt zwraca HTTP 409 z aktualną wartością.

## Konsekwencje
- (+) korektę można cofnąć do wyniku ekstrakcji;
- (+) filtry eksportu automatycznie widzą poprawki;
- ponowne przetworzenie tworzy nowy run; migracja korekt wymaga jawnej decyzji.
