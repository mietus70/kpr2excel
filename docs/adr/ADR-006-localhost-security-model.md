# ADR-006: Model bezpieczeństwa usługi lokalnej

**Status:** zaakceptowany · **Data:** 2026-08-17

## Kontekst
Nawet usługa na loopbacku jest celem: dowolna strona w przeglądarce może próbować
wysłać żądanie na `localhost`, a złośliwy PDF może wyczerpać zasoby.

## Decyzja
- bind `127.0.0.1`; wystawienie w LAN wymaga jawnej zmiany konfiguracji;
- ścisła lista dozwolonych `Host` (obrona przed DNS rebinding) i `Origin`;
- CSRF metodą double-submit cookie dla operacji zmieniających stan;
- CSP `default-src 'self'; connect-src 'self'; img-src 'self' blob: data:`
  — brak CDN, zewnętrznych fontów i analityki;
- frontend zawsze woła URL-e względne; w dev Vite proxuje `/api`, więc tryb
  deweloperski i spakowany są identyczne z punktu widzenia CSP;
- parsowanie PDF w procesie workera, z limitami rozmiaru, stron i leasingu;
- logi zawierają kody i identyfikatory, nigdy treści komórek ani pełnych ścieżek.

## Konsekwencje
- (+) strona zewnętrzna nie wykona operacji zmieniającej dane;
- (+) awaria parsera nie zabija API;
- (−) w dev trzeba jawnie dopuścić origin Vite — kontrolowany wyjątek.
