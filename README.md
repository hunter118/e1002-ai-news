# reTerminal E1002 AI Daily News Display

An end-to-end daily AI news and photo display for the Seeed Studio reTerminal E1002. A GitHub Actions backend turns only the current day's [Juya AI Daily RSS](https://daily.juya.uk/rss.xml) issue into up to 18 source-traceable Chinese stories and deploys one to six e-paper pages to GitHub Pages. A repository folder manages an up-to-20-photo album, ordering, and playback interval through GitHub's normal web interface. The ESP32-S3 remains a small display client: it downloads, validates, caches, and rotates both modes.

## Architecture

```mermaid
flowchart LR
    A[GitHub Actions<br>08:30-14:30 hourly preflight] --> B[Juya RSS]
    B --> C[Parse real stories]
    C --> D[OpenAI<br>dedupe / classify / rank / summarize]
    D --> E[1-18 validated stories<br>today only]
    E --> F[1-6 x 800x480 PNG previews<br>+ native 4bpp E1002 pages]
    F --> G[GitHub Pages<br>manifest.json + page files]
    G --> H[E1002 news A/B cache]
    J[GitHub web<br>gallery/photos + config] --> K[Gallery build Action]
    K --> P[GitHub gallery branch<br>manifest + page files]
    P --> L[E1002 gallery A/B cache]
    H --> I[Spectra 6 display]
    L --> I
    M[LEFT GPIO5<br>previous] --> I
    N[MIDDLE GPIO4<br>next] --> I
    O[RIGHT GPIO3<br>mode] --> I
```

The backend is the only component that sees `OPENAI_API_KEY`. The device only receives public image bytes and their manifest.

## Repository layout

```text
.
├── backend/                  RSS parsing, curation, rendering, tests
├── firmware/                 PlatformIO ESP32-S3 application
│   ├── include/config.h      local credentials; gitignored
│   └── include/config.example.h
├── public/                   deployable news manifest and page assets
├── gallery/                  GitHub-managed source photos and playback config
├── backups/                  local factory flash backup; gitignored
└── .github/workflows/daily.yml
```

Each public page has two files:

- `page_N.png`: an exact 800×480 six-color preview for inspection;
- `page_N.epd`: 192,000 bytes of packed native E1002 pixels (two 4-bit pixels per byte).

The native format uses the nibble values verified in Seeed's official E1002 image pipeline: white `0x0`, green `0x2`, red `0x6`, yellow `0xB`, blue `0xD`, black `0xF`. Four bits are the storage container, not a claim that the panel has 16 colors: only those six codes are valid, so the native information capacity is `log2(6) ≈ 2.58` bits per pixel. Photos use Floyd–Steinberg error diffusion to arrange the six pigments into visually richer intermediate tones; every physical pixel still shows exactly one of the six colors.

News body text and metadata are black. Category accents may use dark blue, red, or green; yellow is excluded from news text because its contrast is poor. Album photos may use all six pigments.

## Verified hardware

The connected device was probed on `/dev/cu.usbserial-110` before any write:

```text
ESP32-S3 QFN56 revision 0.2
8 MB embedded PSRAM
32 MB quad Flash at 3.3 V
USB serial: CH340/UART0 path exposed as /dev/cu.usbserial-110
```

The implementation follows Seeed's current [E1002 hardware documentation](https://wiki.seeedstudio.com/getting_started_with_reterminal_e1002/) and [official E Series example repository](https://github.com/Seeed-Projects/OSHW-reTerminal-Series-E-D). The official button example confirms KEY0/GPIO3, KEY1/GPIO4, and KEY2/GPIO5. From physical left to right this project maps GPIO5 to previous page, GPIO4 to next page, and GPIO3 to news/gallery mode switching.

## Factory flash backup and recovery

No custom firmware may be flashed until the full backup is present and verified. The backup made on this Mac is:

```text
File: backups/e1002_factory_2026-08-31.bin
Expected size: 33,554,432 bytes
SHA-256: `2593f711e53c2537dd24ae7613b83ab23d531d3978cc25ee4ef5b298afe30fa6`
```

Commands used:

```bash
.venv/bin/esptool --port /dev/cu.usbserial-110 chip-id
.venv/bin/esptool --port /dev/cu.usbserial-110 flash-id
.venv/bin/esptool --port /dev/cu.usbserial-110 --baud 115200 read-flash 0x0 0x2000000 backups/e1002_factory_2026-08-31.bin
shasum -a 256 backups/e1002_factory_2026-08-31.bin
```

The backup and checksum sidecar stay under `backups/`, which is gitignored. Never upload them: factory flash can contain device identity or provisioning data.

To restore the verified factory image later:

```bash
shasum -a 256 -c backups/e1002_factory_2026-08-31.sha256
.venv/bin/esptool --port /dev/cu.usbserial-110 --baud 115200 write-flash 0x0 backups/e1002_factory_2026-08-31.bin
```

Keep USB power stable throughout restore. A complete 32 MB restore replaces the custom application, partition table, filesystem, and NVS with the captured factory state.

## Local backend

Python 3.12+ is recommended. The commands used successfully on this Mac were:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m backend.generate
```

Create `.env` locally with only the environment variable (do not commit it):

```dotenv
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-luna
```

`OPENAI_MODEL` is optional. The default `gpt-5.6-luna` is configurable and was selected as the current cost-sensitive model documented by [OpenAI's model catalog](https://developers.openai.com/api/docs/models). The backend uses the official Python SDK's Responses API with Pydantic structured output, following the [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

For parser/render development without an API call:

```bash
.venv/bin/python -m backend.generate --no-openai
```

That mode is intentionally marked development-only. Scheduled publication always uses OpenAI and fails rather than deploying malformed output.

The generator writes inspectable intermediates to gitignored `build/raw_stories.json` and `build/curated.json`. It accepts candidates only from today's selected Juya issue and never supplements from older issues. It publishes all usable stories when there are 18 or fewer, or selects the top 18 when there are more. Each page has up to three stories; unused slots on the final page stay blank.

## Output guarantees

Before `public/` is replaced, the generator enforces:

- between 1 and 18 stories from today's Juya issue only;
- one to six pages, with up to three stories per page;
- unique stable IDs and valid source story IDs;
- each published URL is copied from a source record;
- title, summary, and category constraints;
- 800×480 dimensions and only the six native colors;
- exactly 192,000 bytes per device page;
- a SHA-256 for every device page;
- a valid schema-v1 manifest.

`generation_id` is based on the primary issue date, canonical curated content, and rendered page hashes. Rerunning byte-identical output does not create a false new generation, while a rendering-only color/layout change does reach the device.

## GitHub Actions and Pages

The workflow runs hourly from 08:30 through 14:30 Singapore time (`30 0-6 * * *` UTC). Each scheduled run first reads Juya's RSS and the currently deployed manifest using only the Python standard library. Before today's issue exists it exits without installing dependencies or calling OpenAI; after today's source URL is already deployed, later runs also exit. Thus only the first run that observes a new same-day issue performs generation. This accommodates variable RSS publication times without repeatedly spending API tokens. `workflow_dispatch` remains an explicit forced generation path.

Repository setup:

1. Create/push the repository (the prepared firmware URL assumes `https://github.com/hunter118/e1002-ai-news`).
2. In **Settings → Secrets and variables → Actions**, add repository secret `OPENAI_API_KEY`. Never paste it into source code or firmware.
3. Optionally add repository variable `OPENAI_MODEL`; otherwise `gpt-5.6-luna` is used.
4. In **Settings → Pages → Build and deployment**, choose **GitHub Actions** as the source.
5. Run **Actions → Generate and deploy AI Daily → Run workflow** once.
6. Verify `https://hunter118.github.io/e1002-ai-news/manifest.json` and each listed `pages/page_N.epd` URL.

The workflow tests first, generates into `public/`, checks asset counts and sizes, and only then uploads a Pages artifact. If RSS, OpenAI, validation, or rendering fails, deployment does not run and the previously deployed edition remains available.

## Gallery management through GitHub

The repository's [`gallery/photos`](gallery/photos) folder is the album manager. In GitHub's web interface, use **Add file → Upload files** to add JPG, PNG, or WebP images and commit the change. Because this repository is public, those uploaded source images are public too. Delete a file through its GitHub file page. Filenames are naturally sorted, so numeric prefixes such as `01_`, `02_`, and `03_` set the playback order. Up to 20 photos are accepted. A pre-existing `.epd` may also be retained as a migration source without publishing a new browser-previewable copy.

[`gallery/config.json`](gallery/config.json) stores `interval_seconds`; use `0` for no automatic paging or a value from 10 through 86400 seconds. On each relevant commit, `.github/workflows/gallery-sync.yml` center-crops photos to 800×480, applies Floyd–Steinberg Spectra 6 dithering, validates exact 192,000-byte device pages, and force-replaces the history-free `gallery` delivery branch. The device continues to read `https://raw.githubusercontent.com/hunter118/e1002-ai-news/gallery/manifest.json`. No Sites app, Cloudflare path, personal GitHub token, or local service is involved.

## Firmware configuration, build, and flash

Copy `firmware/include/config.example.h` to the gitignored `firmware/include/config.h`, then set a 2.4 GHz Wi-Fi SSID/password, the news Pages URL, and the validated gallery mirror URL:

```cpp
#define WIFI_SSID "..."
#define WIFI_PASSWORD "..."
#define NEWS_BASE_URL "https://hunter118.github.io/e1002-ai-news/"
#define GALLERY_BASE_URL "https://raw.githubusercontent.com/hunter118/e1002-ai-news/gallery/"
```

Build command used successfully on this Mac:

```bash
PLATFORMIO_CORE_DIR="$PWD/.platformio-core" .venv/bin/pio run -d firmware
```

After the factory backup checksum is verified, upload and monitor:

```bash
PLATFORMIO_CORE_DIR="$PWD/.platformio-core" .venv/bin/pio run -d firmware --target upload
PLATFORMIO_CORE_DIR="$PWD/.platformio-core" .venv/bin/pio device monitor --port /dev/cu.usbserial-110 --baud 115200
```

E1001/E1002 USB is connected through a CH340 bridge to UART0. The application therefore logs with `Serial1` on RX GPIO44 / TX GPIO43, matching Seeed's official examples.

## Device behavior

At every cold boot or reset the firmware mounts LittleFS, loads the last complete news and gallery caches, turns on Wi-Fi, checks both manifests, renders the active page, and enters deep sleep. RTC memory retains the active mode, both page indices, and the remaining page/update countdowns. The next timer wake is whichever comes first: the active mode's automatic page interval or the 60-minute update interval. News therefore wakes every ten minutes to turn one page and every sixth timer wake also checks both manifests. The album honors `gallery/config.json`, including `0` for no automatic paging. Every timer path returns to deep sleep after its task, including unchanged and failed update checks.

All three front buttons are deep-sleep wake sources. A wake press performs its normal action (LEFT previous, MIDDLE next, RIGHT mode), then keeps the device interactive for three minutes after the most recent button press. Wi-Fi stays off during this interaction. At the end of the inactivity window the current mode/page state is retained and the device sleeps again. A button wake can slightly postpone the relative page/update countdown because strict wall-clock alignment is intentionally not required.

News and gallery share one Wi-Fi connection for each update batch; immediately afterward the station disconnects and the radio is disabled. A brand-new or unreadable cache partition is formatted only after a logged mount failure. Each mode has independent A/B slots. Every page must have the expected length and SHA-256; only after a whole generation validates does one NVS value atomically switch that mode's active slot. A partial download is discarded while the old complete slot remains untouched.

The synchronous e-paper refresh is guarded by `displayRefreshing`, so button and timer events cannot overlap a refresh. LEFT/MIDDLE wrap through the active mode's pages, while RIGHT switches between news and gallery.

No always-on local service is required. GitHub Actions performs news generation and gallery conversion, GitHub Pages serves news assets, the `gallery` branch serves album assets, and the powered E1002 polls both public delivery endpoints over Wi-Fi. The Mac, local virtual environment, `.env`, and development servers may all be shut down after deployment. Autonomous operation still depends on device power/Wi-Fi, an enabled GitHub Actions/Pages setup, a valid `OPENAI_API_KEY` repository secret, and availability of the external cloud services.

No SD card is required. One cached device page is exactly 192,000 bytes. Ten gallery pages need 1.92 MB, or 3.84 MB while old and new A/B generations coexist. The maximum six news pages add about 2.30 MB across A/B slots, for roughly 6.14 MB total. The device reserves a 28 MB LittleFS partition, so the current 20-photo firmware limit remains comfortably within internal flash capacity.

## Troubleshooting

- **`Operation not permitted` opening the serial port:** grant the terminal/Codex process access to removable devices or serial ports, then retry `/dev/cu.usbserial-110`.
- **Factory backup stops with serial corruption:** use the proven 115200 baud command; the faster 460800 attempt was unstable on this cable/device.
- **Port busy:** close PlatformIO monitor, `screen`, Arduino IDE, or another serial terminal.
- **No serial logs:** monitor the CH340 port at 115200; the firmware intentionally uses `Serial1`, not USB CDC `Serial`.
- **Wi-Fi fails:** the ESP32-S3 needs 2.4 GHz Wi-Fi. Existing cached pages continue rotating.
- **No valid cache on first boot:** confirm both manifests are public and `NEWS_BASE_URL` / `GALLERY_BASE_URL` end with `/`.
- **New edition is rejected:** compare the serial SHA/size error with `public/manifest.json`; all `.epd` files must be 192,000 bytes.
- **GitHub Action does not deploy news:** confirm Pages is set to GitHub Actions and the repository has the `OPENAI_API_KEY` secret.
- **A committed photo does not appear:** open **Actions → Build E1002 gallery** and confirm the latest run succeeded; then wait for the device's hourly check or restart it.
- **Slow screen updates:** a full Spectra 6 refresh is intentionally slow. Do not repeatedly reset or press navigation during refresh.
