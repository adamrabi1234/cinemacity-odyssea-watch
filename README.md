# Cinema City Odyssea watch

Watcher sleduje veřejné Cinema City API a hledá představení filmu **Odyssea** v Praze. Aplikace je připravená jako samostatná Docker Compose služba pro Coolify: kontrolu provede hned po startu a potom ji opakuje v nastaveném intervalu. GitHub slouží pouze jako zdroj kódu; GitHub Actions nejsou potřeba.

## Veřejné endpointy

Při výchozím nastavení jsou data dostupná na:

- `http://SERVER_IP:18080/latest.json` – poslední úspěšný snapshot, ideální pro pravidelné čtení
- `http://SERVER_IP:18080/history.json` – historie změn
- `http://SERVER_IP:18080/healthz` – krátká kontrola stavu
- `http://SERVER_IP:18080/` – přehled endpointů

Server je pouze pro čtení. Odpovědi mají `Cache-Control: no-store`, aby čtenář nedostal starou kopii.

## Nasazení v Coolify

1. V Coolify zvolte **New Resource → Public Repository**.
2. Použijte repozitář `https://github.com/adamrabi1234/cinemacity-odyssea-watch` a větev `main`.
3. Jako build pack zvolte **Docker Compose** a soubor `compose.yaml`.
4. Doménu nevyplňujte. Aplikace zveřejní přímo port serveru.
5. Volitelně nastavte environment proměnné:
   - `PUBLIC_PORT=18080` – port na veřejné IP serveru
   - `WATCH_INTERVAL_SECONDS=900` – kontrola každých 15 minut
6. Proveďte deploy a zvenku ověřte `http://SERVER_IP:18080/healthz`.

Coolify Scheduled Tasks nejsou potřeba. Smyčka kontrol je součástí kontejneru a služba se po pádu nebo restartu serveru automaticky znovu spustí.

### Volba volného portu

Výchozí `18080` je schválně méně běžný než `80`, `443` nebo `8000`. Před deployem lze na serveru zkontrolovat jeho dostupnost:

```bash
sudo ss -ltn '( sport = :18080 )'
```

Prázdný výstup znamená, že na portu nic neposlouchá. Pokud je obsazený, nastavte v Coolify například `PUBLIC_PORT=18081` nebo `PUBLIC_PORT=18082` a stejný port použijte v URL. Port musí být povolený také ve firewallu nebo u poskytovatele serveru; například s UFW:

```bash
sudo ufw allow 18080/tcp
```

## Trvalá data

Docker volume `cinema-watch-data` je připojený do `/app/data`. Snapshoty tedy zůstanou zachované při novém deployi nebo výměně kontejneru. Soubory v Git repozitáři slouží jen jako počáteční data při prvním vytvoření volume.

Pokud kontrola Cinema City API dočasně selže, HTTP server dál poskytuje poslední platný snapshot a další kontrolu zkusí po uplynutí intervalu.

## Lokální spuštění

```bash
docker compose up --build -d
curl --fail http://localhost:18080/healthz
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

## Poznámka k přístupu z ChatGPT

Nejdřív vyzkoušejte přímou adresu `http://SERVER_IP:PUBLIC_PORT/latest.json`. Pokud služba, která data čte, odmítá nešifrované HTTP nebo nestandardní port, není potřeba kupovat doménu: jako další krok lze použít bezplatný hostname ve tvaru `watch.SERVER_IP.sslip.io` a HTTPS proxy v Coolify.

Repozitář neposílá notifikace a nic necommituje automaticky zpět na GitHub.
