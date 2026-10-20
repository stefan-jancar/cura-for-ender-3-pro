# Kompilátor Cura → Ender-3 Pro

Aplikácia, ktorá z modelu stiahnutého z internetu (pripraveného pre inú tlačiareň či slicer)
spraví hotové súbory pre **Creality Ender-3 Pro**:

- načíta **STL, OBJ, 3MF** (aj projekty **Bambu Studio / OrcaSlicer / PrusaSlicer** s viacerými platňami) a **ZIP**,
- z odkazu stiahne model sám – **Printables**, **Thingiverse**, priame odkazy na súbor,
- diely **natočí a rozloží na podložku 220 × 220 mm** (čo najmenej podložiek, diely z rôznych materiálov zvlášť),
- diely **väčšie ako podložka rozreže** na kusy na zlepenie,
- pre každú podložku sám rozhodne o **podperách** (len pri previsoch nad podložkou) a o **brime** (malé, úzke a vysoké diely),
- **nareže G-code cez CuraEngine z tvojej Cura 5.12** s optimálnymi nastaveniami pre Ender-3 Pro (PLA, PETG, TPU, ABS × 0,28 / 0,2 / 0,12 mm),
- k tomu pripraví **3MF na otvorenie v Cure** a **.curaprofile** s rovnakými nastaveniami.

| Časť | Technológia |
|---|---|
| Jadro (`kompilator/`) | Python – trimesh, manifold3d (rezanie), vlastný prepočet nastavení Cura, CuraEngine |
| PC aplikácia (`desktop/`) | Python – lokálny server + okno (pywebview), inštalátor `.exe` z GitHub Actions |
| Web + fronta (`api/`, `server/`, `public/`) | FastAPI na Verceli, PWA bez buildu, dáta v GitHub repozitári (ako Môj priestor) |
| Android (`mobile/`) | Capacitor → APK z GitHub Actions |

## Ako to funguje

```
mobil / web (Vercel)  ──odkaz alebo súbor──▶  fronta (GitHub repo s dátami)
                                                   │  PC každých 15 s skontroluje frontu
PC aplikácia  ◀──────────────────────────────────────┘
  └─ stiahne model → rozloží/rozreže → CuraEngine → G-code
       ├─ uloží do Dokumenty\Ender3Pro G-code\<názov>\  (+ tlačidlo „Na SD kartu“)
       └─ nahrá výsledky späť → v mobile sa dajú stiahnuť / poslať ďalej
```

Na PC funguje aplikácia aj úplne sama (bez Vercelu): vlož odkaz alebo pretiahni súbor do okna.

---

## 1. PC aplikácia (Windows)

Potrebuješ nainštalovanú **UltiMaker Cura 5.12** (alebo novšiu 5.x) v `C:\Program Files`.

- **Inštalátor:** GitHub → *Releases* → **Kompilátor Cura – Windows N** → `KompilatorCura-Setup.exe`.
- **Bez inštalácie:** `KompilatorCura-portable.zip` → rozbaľ → `KompilatorCura.exe`.

Tlačidlá pri každej podložke: **Na SD kartu** (skopíruje G-code na vloženú kartu), **Uložiť G-code**,
**Otvoriť v Cure** (3MF s rozložením – ak chceš niečo doladiť ručne), **Priečinok**.

Spustenie zo zdrojákov:

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-desktop.txt
.venv\Scripts\python desktop\app.py
```

## 2. Web + fronta na Verceli

1. Dáta: použi existujúci repozitár s dátami (napr. `mojprojekt-data`) – kompilátor si v ňom robí priečinok `kompilator/`.
   Alebo si vytvor nový súkromný repozitár a fine-grained token s právom **Contents: Read and write**.
2. [vercel.com/new](https://vercel.com/new) → importuj tento repozitár (Framework preset: *Other*).
3. **Environment Variables:**

| Premenná | Hodnota |
|---|---|
| `APP_PASSWORD` | heslo do aplikácie (povinné) |
| `GITHUB_TOKEN` | token k repozitáru s dátami |
| `GITHUB_DATA_REPO` | `stefan-jancar/mojprojekt-data` |
| `GITHUB_DATA_PREFIX` | *(voliteľné)* priečinok v repozitári, predvolene `kompilator` |
| `GITHUB_DATA_BRANCH` | *(voliteľné)* vetva, predvolene `main` |

4. Deploy → aplikácia beží na `https://<nazov>.vercel.app`.
5. V PC aplikácii: **Nastavenia → Fronta z mobilu** → zadaj adresu a heslo → **Prihlásiť**.

Súbory idú cez Vercel po kúskoch 3 MB (limit Vercelu je 4,5 MB), výsledky sú komprimované (gzip).

## 3. Android APK

1. V tomto repozitári: **Settings → Secrets and variables → Actions**
   - *Variables* → `APP_URL` = `https://<nazov>.vercel.app`
   - *Secrets* → `KEYSTORE_PASSWORD` = rovnaké heslo ako v repozitári `mojprojekt` (stály podpis, aktualizácie bez odinštalovania)
2. **Actions → Android APK → Run workflow.**
3. Po ~5 minútach je v **Releases** `KompilatorCura.apk`.

V mobile: vlož odkaz (alebo v Chrome *Zdieľať → Kompilátor*, ak máš nainštalovanú PWA) → **Prekompilovať**.
Hotový G-code sa dá **poslať ďalej** (Quick Share, Gmail, Disk…), na PC ho ale aplikácia uloží sama.

## Nastavenia tlače

Predvoľby sú v [`kompilator/config.py`](kompilator/config.py) – základ pre Ender-3 Pro (Bowden, tryska 0,4,
retrakcia 5 mm / 45 mm/s, vonkajšia stena 30 mm/s, 3 steny, gyroid 20 %, kompenzácia elephant foot −0,1 mm)
a materiály PLA / PETG / TPU / ABS. Štart a koniec G-code sú z oficiálnej definície Ender-3 Pro v Cure.

## Vývoj

```bash
.venv\Scripts\python -m unittest discover -s tests -v   # testy (slicovanie sa preskočí bez Cury)
.venv\Scripts\python tools\build_exe.py                 # dist\KompilatorCura\KompilatorCura.exe
.venv\Scripts\python tools\make_icons.py                # ikony (potrebuje Pillow)
```

Lokálna simulácia Vercelu: `APP_PASSWORD=test uvicorn tools.dev_cloud:app --port 8766`.
