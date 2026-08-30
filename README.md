# Cinema City Odyssea watch

Watcher sleduje veřejné Cinema City API a hledá představení filmu **Odyssea** v Praze. Aplikace je připravená jako samostatná Docker Compose služba pro Coolify: kontrolu provede hned po startu a potom ji opakuje podle adaptivního rozvrhu. GitHub slouží pouze jako zdroj kódu; GitHub Actions nejsou potřeba.

## Veřejné endpointy

Po přiřazení domény v Coolify jsou data dostupná na:

- `https://VAŠE_DOMÉNA/latest.json` – poslední úspěšný snapshot, ideální pro pravidelné čtení
- `https://VAŠE_DOMÉNA/history.json` – historie změn
- `https://VAŠE_DOMÉNA/healthz` – krátká kontrola stavu
- `https://VAŠE_DOMÉNA/` – přehled endpointů

Server je pouze pro čtení. Odpovědi mají `Cache-Control: no-store`, aby čtenář nedostal starou kopii.

## Nasazení v Coolify

1. V Coolify zvolte **New Resource → Public Repository**.
2. Použijte repozitář `https://github.com/adamrabi1234/cinemacity-odyssea-watch` a větev `main`.
3. Jako build pack zvolte **Docker Compose** a soubor `compose.yaml`.
4. U služby `cinema-watch` v poli **Domains** použijte **Generate Domain**, případně zadejte vlastní subdoménu.
5. Protože aplikace uvnitř kontejneru poslouchá na portu 8000, musí mít hodnota v Coolify tvar `https://VAŠE_DOMÉNA:8000`. Číslo zde pouze říká proxy, na který interní port má požadavky směrovat; návštěvník ho ve výsledné URL nepoužívá.
6. Proměnnou `WATCH_INTERVAL_SECONDS` nechte prázdnou pro doporučený adaptivní rozvrh. Kladné celé číslo vynutí pevný interval v sekundách.
7. Proveďte deploy a zvenku ověřte `https://VAŠE_DOMÉNA/healthz`.

Coolify Scheduled Tasks nejsou potřeba. Smyčka kontrol je součástí kontejneru a služba se po pádu nebo restartu serveru automaticky znovu spustí.

### Adaptivní rozvrh kontrol

Časy se vždy vyhodnocují v časové zóně `Europe/Prague`:

| Období | Interval |
| --- | ---: |
| Pondělí 18:00–24:00 | 10 minut |
| Úterý 06:00–14:00 | 10 minut |
| Úterý 14:00–22:00 | 30 minut |
| Ostatní dny 07:00–23:00 | 1 hodina |
| Noc mimo hlavní publikační okno | 4 hodiny |

Cinema City uvádí, že nový program na období čtvrtek–středa zveřejňuje v úterý; v nápovědě také zmiňuje pondělí večer nebo úterý ráno. Rozvrh proto kontroluje nejčastěji v tomto publikačním okně a mimo něj omezuje zbytečné API požadavky. Při přechodu do rychlejšího okna se čekání automaticky zkrátí, takže například kontrola v úterý před 06:00 toto okno nepřeskočí.

### Proč nehrozí konflikt portů

Compose soubor nepublikuje port 8000 přímo na hostitelském serveru. Port je dostupný jen v interní Docker síti a Coolify proxy rozlišuje aplikace podle domén. Více služeb proto může současně používat interní port 8000 bez vzájemného konfliktu. Ve firewallu není potřeba otevírat žádný nový port; veřejný provoz jde přes standardní HTTPS port 443.

## Trvalá data

Docker volume `cinema-watch-data` je připojený do `/app/data`. Snapshoty tedy zůstanou zachované při novém deployi nebo výměně kontejneru. Soubory v Git repozitáři slouží jen jako počáteční data při prvním vytvoření volume.

Pokud kontrola Cinema City API dočasně selže, HTTP server dál poskytuje poslední platný snapshot a další kontrolu zkusí po uplynutí intervalu.

## Lokální spuštění

```bash
docker compose up --build -d
docker compose exec cinema-watch python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"
docker compose logs -f cinema-watch
```

Zastavení bez smazání uložených dat:

```bash
docker compose down
```

## Konfigurace vyhledávání

Soubor `config.json` určuje kino, hledaný název filmu a požadovaný formát. Současné nastavení:

- Cinema City API pro ČR (`quickbook/10101`)
- kino, jehož název obsahuje `Praha Flora` (fallback ID `1052`)
- titul, jehož název obsahuje `Odyssea`
- povinný atribut `70-mm`
- pouze sál IMAX

## Kontrola bez Dockeru

```bash
python -m pip install -r requirements.txt
python src/watch.py
python -m unittest discover -s tests -v
```

## Přístup z ChatGPT

Pro scheduler použijte `https://VAŠE_DOMÉNA/latest.json`. HTTPS doména přes Coolify proxy je vhodnější než přímá IP adresa a nestandardní veřejný port.

Repozitář neposílá notifikace a nic necommituje automaticky zpět na GitHub.
