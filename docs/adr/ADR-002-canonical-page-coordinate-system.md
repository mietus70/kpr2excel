# ADR-002: Kanoniczny układ współrzędnych strony

**Status:** zaakceptowany · **Data:** 2026-08-17

## Kontekst
PyMuPDF zwraca tekst i obiekty rysunkowe we współrzędnych strony *nieobróconej*,
podczas gdy `page.rect` opisuje stronę już obróconą. Ręczne przeliczanie rotacji
prowadziło do podwójnej transformacji i całkowicie błędnej geometrii.

## Decyzja
Kanoniczny układ to przestrzeń wyświetlana (po rotacji), znormalizowana do `0..1`.
Każdy prostokąt przechodzi przez `page.rotation_matrix` — natywny most biblioteki
— zamiast własnej arytmetyki. Domena nigdy nie widzi współrzędnych w punktach.

## Konsekwencje
- (+) poprawna obsługa stron 90/180/270 bez rozgałęzień w kodzie domenowym;
- (+) prostokąty niezależne od rozmiaru strony, gotowe do nakładki SVG w UI;
- adapter PDF jest jedynym miejscem znającym bibliotekę i rotację.
