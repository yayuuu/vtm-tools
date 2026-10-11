# vtm-tools

Dodatkowe narzędzia dla VTM. Python 3.11+, Linux, bez zależności od Debiana,
CephFS, nazwy komputera lub konkretnego katalogu użytkownika. Nie wymagają
uruchamiania `vtm-desktop`; działają także w istniejącym VTM przez SSH.
Repozytorium zawiera źródła, instalację i przenośne testy. Receptury paczek
Debiana znajdują się oddzielnie w projekcie `debian-repo`.

## Instalacja

```sh
make check
sudo make install PREFIX=/usr/local
# albo bez uprawnień administratora:
make install PREFIX="$HOME/.local"
```

Dodaj `$HOME/.local/bin` do PATH dla instalacji użytkownika. Pakiet z repo APT:
`sudo apt install vtm-tools`. Zależności zależą od używanej funkcji:

| Narzędzie | Dodatkowe programy |
| --- | --- |
| `vtm-terminal`, `vtm-lua` | VTM z API Lua `vtm.desktop` |
| `setwallpaper` | Chafa, FFmpeg; VTM do przeładowania na żywo |
| `vtmpanel` | Linux `/proc`, `/sys` |
| `screenshow`, `kmsgrab` | FFmpeg; opcjonalnie grim do Wayland, pactl do audio |
| `vtm-clipboard` | wl-copy/wl-paste albo xclip i działający serwer X11 |
| `vtm-clipboard-server` | Xvfb, xauth; standardowy schowek X11 dla kmscon |

## Nowe okno

```sh
vtm-terminal -- nvim ./plik.cpp
vtm-terminal --title 'Monitor' --size 100:35 -- htop
vtm-terminal --cwd ~/src/vtm -- bash -l
vtm-terminal # powłoka użytkownika
```

Zachowuje bieżący katalog. Argumenty są kodowane osobno, bez interpretacji
przez dodatkową powłokę. Powłoka jest używana tylko jeśli sam ją wywołasz:
`vtm-terminal -- sh -c 'polecenie1; polecenie2'`.

Sesja: `--pin`, potem `VTM_DESKTOP_PIN`, potem `[vtm].session` w konfiguracji.
Brak wartości oznacza standardową sesję VTM. `vtm-desktop` ustawia zmienną
automatycznie. Nie jest tworzona druga sesja pulpitu.

`vtm-run` i `vtm-open` są już częścią **xdg-desktop-portal-vtm**, a otwieranie
linków i picker Elio należą do tego projektu. Nie instalujemy ich ponownie
ani nie zastępujemy systemowych `xdg-open`, `xclip` czy `chafa` wrapperami.
Start sesji i manager logowania należą do **vtm-desktop**. Codex/endcord i
ich indywidualne launchery nie są zależnościami tych narzędzi.

## Tapeta

```sh
setwallpaper ~/Pictures/tapeta.jpg
setwallpaper --mode contain ~/Pictures/tapeta.jpg
setwallpaper --mode tile --size 80x25 ~/Pictures/kafel.png
setwallpaper --restore --no-reload
```

Tryby: cover, contain, stretch, tile. Rozmiar pulpitu i komórki tekstowej jest
odczytywany z zewnętrznego terminala sesji VTM. Można nadpisać `--size 160x48`
(komórki) i `--cell-size 8x16` (piksele). Tapeta oraz parametry są zapisywane
w katalogu `$XDG_CONFIG_HOME/vtm` lub `~/.config/vtm`. Modyfikowany jest tylko
`desktop/background/tile` w ustawieniach VTM. Istniejące aplikacje pozostają.
`--no-reload` zapisuje ustawienia bez łączenia z pulpitem. `vtm-desktop`
odtwarza zapisaną tapetę przy starcie w aktualnym rozmiarze konsoli.

## Zrzuty i nagrywanie

```sh
screenshow
screenshow --device /dev/dri/card1 --directory ~/Pictures/Screenshots
screenshow --no-clipboard --output ./screen.png
kmsgrab --sudo # nagrywanie KMS z systemowym audio
kmsgrab /dev/dri/card1 --audio-delay 1000 --output ./nagranie.mkv
kmsgrab --no-audio --audio-delay 0
```

Screenshoty trafiają domyślnie do Pictures/Screenshots, nagrania do
Videos/vtm-recordings (katalogi XDG). Zrzut kopiuje **image/png** do schowka.
Przy braku schowka plik pozostaje zapisany, a polecenie zwraca błąd.
Automatyczny backend: grim w Wayland, rzeczywisty ekran X11, potem KMS.
W kmscon serwer Xvfb przechowuje schowek, ale **nie jest ekranem do przechwycenia**.
Karta DRM jest wybierana z aktywnych złączy; `--device` ją nadpisuje.

FFmpeg kmsgrab wymaga uprawnień DRM/root lub CAP_SYS_ADMIN, których te narzędzia
nie nadają. `kmsgrab --sudo` pozwala świadomie podnieść uprawnienia procesu
nagrywania, zachowując dostęp do dźwięku użytkownika. Automatyczny PrintScreen
w sesji nieuprzywilejowanej wymaga uprawnionego narzędzia capture albo
odpowiedniej konfiguracji systemowej. Nie zmieniamy uprawnień całego FFmpeg.
Domyślne opóźnienie audio 1000 ms zachowuje poprawkę używaną w pierwotnym
skrypcie; na innej maszynie można ustawić inną wartość. Nagrywanie kończy Ctrl+C.

## Schowek w kmscon

```sh
vtm-clipboard --type image/png screen.png
printf 'tekst' | vtm-clipboard
vtm-clipboard --paste
```

Używa standardowego wl-clipboard lub X11/xclip. Dla sesji bez serwera X11
`vtm-clipboard-server --write-env "$XDG_RUNTIME_DIR/clipboard.env"` uruchamia
prywatny Xvfb na wolnym numerze, z autoryzacją i bez nasłuchu TCP. Po pojawieniu
się pliku launcher wczytuje go przez `. "$XDG_RUNTIME_DIR/clipboard.env"`
**przed uruchomieniem VTM**, żeby aplikacje odziedziczyły DISPLAY/XAUTHORITY.
Proces pozostaje aktywny do zakończenia sesji; jego zakończenie sprząta własne
pliki. `vtm-desktop` wykonuje to automatycznie w sesji kmscon, jeśli nie ma
istniejącego wyświetlacza. Schowek należy do maszyny, na której działają te
narzędzia: przez SSH nie przekazuje automatycznie obrazów na komputer klienta.

## Pasek i Lua

`vtmpanel` pokazuje CPU, RAM, interfejs i transfer. Wybiera kartę z trasy
domyślnej, rozpoznaje bridge/bond/VLAN i pozwala nadpisać `--interface` lub
`VTM_NET_INTERFACE`. Oba kierunki przełączają się na MiB/s, gdy choć jeden
przekroczy 10 MiB/s. `vtmpanel --once` umożliwia jednorazowy odczyt.

```sh
vtm-lua 'vtm.desktop.Deface();'
printf 'return 1 + 1\n' | vtm-lua -
```

Lua działa w istniejącym pulpicie. Metody wymagające aktywnego urządzenia
wejściowego (gear), np. Shutdown/RunBackground, nie zawsze mogą być wykonane
z monitora. Polecenia pochodzą z zaufanego lokalnego użytkownika.

## Konfiguracja

Wzór: [config.example.toml](config.example.toml). Domyślne ustawienia systemowe:
`/etc/xdg/vtm-tools/config.toml`; ustawienia użytkownika:
`~/.config/vtm-tools/config.toml`. Obsługiwane XDG_CONFIG_HOME/XDG_CONFIG_DIRS.
Zmienne SCREENSHOW_DIR/DEVICE/DELAY, KMSGRAB_DIR/AUDIO_DELAY_MS oraz
VTM_DESKTOP_PIN/VTM_BINARY zachowują możliwość sterowania ze środowiska.
