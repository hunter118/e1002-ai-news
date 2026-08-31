# reTerminal E1002 AI Daily News Display — Implementation Task

## 0. Goal

Build a complete end-to-end AI news display system for a **Seeed Studio reTerminal E1002** connected to this MacBook.

The device should display a curated daily AI news digest sourced from:

```text
https://daily.juya.uk/rss.xml
```

Required behavior:

```text
Every day 07:30 Asia/Singapore
        ↓
Fetch latest Juya AI Daily issue
        ↓
Use OpenAI API to deduplicate / classify / summarize / rank
        ↓
Select exactly 18 stories
        ↓
Group dynamically — do NOT force six fixed categories
        ↓
6 pages × 3 stories/page
        ↓
Render six 800×480 e-paper-optimized pages
        ↓
E1002 downloads and caches them
        ↓
Display page 1
        ↓
Automatically advance every 10 minutes
        ↓
Left button = previous page
Right button = next page
```

Do not add unnecessary features beyond what is required here.

---

# 1. Important Working Style

Please implement as much of this project autonomously as possible.

Do NOT walk the user through one command at a time.

You may:

- inspect the Mac environment;
- install normal development dependencies;
- create the project structure;
- write code;
- run tests;
- compile firmware;
- inspect the connected serial device;
- back up the device;
- prepare GitHub Actions;
- prepare deployment;
- create documentation.

Only stop and ask the user when human intervention is genuinely required, for example:

- entering Wi-Fi credentials;
- entering an OpenAI API key / GitHub Secret;
- pressing a physical boot/reset button;
- choosing a GitHub repository/account if this cannot be inferred;
- physically reconnecting the device.

The device is currently connected to this MacBook.

Likely serial device:

```text
/dev/cu.usbserial-110
```

Other currently observed serial ports were:

```text
/dev/cu.Bluetooth-Incoming-Port
/dev/cu.MOONDROPEVO2
/dev/cu.debug-console
/dev/cu.usbserial-110
```

Assume `/dev/cu.usbserial-110` is the E1002, but verify it before writing anything.

---

# 2. Hardware

Target:

```text
Seeed Studio reTerminal E1002
```

Expected hardware:

```text
ESP32-S3
8 MB PSRAM
32 MB Flash
800 × 480 Spectra 6 color e-paper
```

Verify important hardware assumptions against current official Seeed documentation/repositories before flashing.

Expected button mapping from Seeed documentation:

```text
Left  button: GPIO 5
Middle button: GPIO 4
Right button: GPIO 3
```

Verify before implementation.

The screen requires a slow full refresh and does not behave like an LCD.

Design firmware accordingly:

- never trigger overlapping refreshes;
- debounce buttons;
- ignore/reject duplicate refresh commands while a refresh is active;
- no animations;
- no partial-refresh assumptions.

---

# 3. Architecture

Use two independent components.

## A. Backend / Daily Generator

Runs in GitHub Actions.

Responsibilities:

```text
Juya RSS
↓
extract today's issue
↓
parse individual news stories
↓
OpenAI processing
↓
exactly 18 curated stories
↓
6 logical pages
↓
render six 800×480 images
↓
generate manifest.json
↓
publish as static files using GitHub Pages
```

## B. E1002 Firmware

Responsibilities only:

```text
connect Wi-Fi
↓
fetch manifest.json
↓
download/cache six images
↓
render image
↓
manual navigation
↓
10-minute automatic navigation
```

Do NOT run:

- RSS parsing;
- OpenAI calls;
- summarization;
- classification;
- complex text layout

on the ESP32.

The E1002 should be a simple display client.

---

# 4. Project Structure

Use approximately:

```text
e1002-ai-news/
├── README.md
├── .gitignore
│
├── backend/
│   ├── requirements.txt
│   ├── fetch_juya.py
│   ├── parse_issue.py
│   ├── curate.py
│   ├── render.py
│   ├── generate.py
│   ├── models.py
│   └── tests/
│
├── firmware/
│   ├── platformio.ini
│   ├── include/
│   │   ├── config.example.h
│   │   └── config.h              # gitignored
│   └── src/
│       └── main.cpp
│
├── public/
│   ├── manifest.json
│   └── pages/
│       ├── page_1.png
│       ├── page_2.png
│       ├── page_3.png
│       ├── page_4.png
│       ├── page_5.png
│       └── page_6.png
│
└── .github/
    └── workflows/
        └── daily.yml
```

Adjust filenames where technically justified, but preserve the separation between backend, generated assets, and firmware.

---

# 5. FIRST: Back Up the Original E1002 Firmware

Before the first firmware write, make a complete backup of the factory flash.

This is mandatory.

Use `esptool` or the current Espressif equivalent.

First verify:

```bash
esptool --port /dev/cu.usbserial-110 chip-id
esptool --port /dev/cu.usbserial-110 flash-id
```

Adapt syntax if the installed esptool version uses newer command conventions.

Then dump the entire detected flash.

Store it somewhere such as:

```text
backups/e1002_factory_YYYY-MM-DD.bin
```

Calculate SHA-256.

Example:

```bash
shasum -a 256 backups/e1002_factory_YYYY-MM-DD.bin
```

Add:

```text
backups/
```

to `.gitignore`.

Never:

- commit this backup;
- upload it publicly;
- send it to external services.

It may contain device identifiers, Wi-Fi information or provisioning information.

Before flashing custom firmware, verify:

1. backup exists;
2. backup size looks correct;
3. SHA-256 has been recorded.

Also document restoration instructions in `README.md`.

---

# 6. Juya RSS Processing

Input:

```text
https://daily.juya.uk/rss.xml
```

This RSS is not intended to be displayed directly.

The backend should treat it as a source document.

The Juya daily issue may contain many stories organized into sections.

Extract as much useful structure as reasonably possible:

```json
{
  "title": "...",
  "description": "...",
  "source": "...",
  "url": "...",
  "original_section": "..."
}
```

Preserve original URLs whenever available.

Do not scrape unrelated historical issues every day.

Normally:

1. fetch RSS;
2. identify today's newest issue;
3. download/parse that issue if necessary;
4. extract its individual news entries.

Timezone:

```text
Asia/Singapore
```

If there is no issue for the current Singapore date yet, use the newest available issue rather than failing completely.

Make this fallback visible in logs.

---

# 7. OpenAI Processing

Use the official OpenAI Python SDK.

API key:

```text
OPENAI_API_KEY
```

must come exclusively from environment variables / GitHub Secrets.

Never commit the key.

Do not expose the API key to:

- firmware;
- GitHub Pages;
- generated HTML;
- manifest;
- images.

Make model configurable, e.g.:

```text
OPENAI_MODEL
```

Use a current cost-effective text model supported by the user's API account.

Check current OpenAI API documentation when implementing structured output.

Prefer JSON-schema / structured output if currently supported.

---

# 8. GPT Task

GPT should NOT invent news.

It should transform the Juya source material.

Required operations:

1. remove obvious duplicate stories;
2. merge stories referring to the same event;
3. classify stories dynamically;
4. assign importance/relevance scores;
5. produce concise Chinese display text;
6. select exactly 18 stories;
7. arrange them into six pages of three stories.

Do NOT force:

```text
6 categories = 6 pages
```

Categories are dynamic.

For example one day could naturally contain:

```text
模型发布
Agent / Coding
研究
产品
产业动态
开源
```

while another day might contain only four meaningful categories.

Page composition should follow the actual news.

A page may contain multiple related categories if appropriate.

---

# 9. Required Curated Story Schema

Something similar to:

```json
{
  "id": "stable-id",
  "title": "简短中文标题",
  "summary": "一到两句简明中文摘要。",
  "category": "模型发布",
  "source": "OpenAI",
  "url": "https://...",
  "importance": 0.91
}
```

Constraints:

- exactly 18 final stories;
- each story based on a real source entry;
- no invented facts;
- avoid excessive hype;
- preserve important numbers/model names;
- title should fit an e-paper display;
- summary should normally be around 1–2 short Chinese sentences.

If GPT returns fewer/more than 18 due to validation failure, automatically retry or deterministically repair.

Do not silently publish a malformed daily result.

---

# 10. Page Layout

Target resolution:

```text
800 × 480
```

Exactly:

```text
6 pages
3 stories per page
```

Each page should contain roughly:

```text
┌────────────────────────────────────────────────────────────┐
│ AI DAILY                    31 AUG 2026            1 / 6   │
├────────────────────────────────────────────────────────────┤
│ [CATEGORY]                                                 │
│ News title                                                 │
│ concise one/two-line summary                               │
│ Source                                                     │
├────────────────────────────────────────────────────────────┤
│ [CATEGORY]                                                 │
│ News title                                                 │
│ concise one/two-line summary                               │
│ Source                                                     │
├────────────────────────────────────────────────────────────┤
│ [CATEGORY]                                                 │
│ News title                                                 │
│ concise one/two-line summary                               │
│ Source                                                     │
└────────────────────────────────────────────────────────────┘
```

Do not add:

- weather;
- stock prices;
- QR codes;
- clocks;
- unrelated widgets;
- advertisements;
- decorative dashboards.

Keep the design highly readable.

---

# 11. E-Paper Rendering

Prefer server-side rendering.

Render all typography on the GitHub runner rather than on the ESP32.

Use a Chinese-capable font installed during the GitHub Action, such as Noto CJK.

Do not require the repository to contain proprietary/local font files.

Optimize for a six-color e-paper panel.

Prefer:

- white background;
- black primary text;
- sparse use of the panel's supported accent colors;
- no subtle gray;
- no gradients;
- no shadows;
- high contrast;
- large readable Chinese type.

Apply appropriate palette conversion / dithering if required by the E1002 display pipeline.

Text readability is more important than image fidelity.

---

# 12. Image Format

Investigate the current official Seeed E1002 Arduino/PlatformIO examples and choose the most reliable image format/decoder.

Prefer a format that:

- preserves text edges;
- can be decoded reliably on ESP32-S3;
- fits comfortably in 32 MB flash;
- works with PSRAM;
- works with the official/commonly supported E1002 display library.

PNG is acceptable if stable.

If BMP or another format is materially simpler/more robust on this device, use it instead.

The backend and firmware must agree on the format.

Do not optimize prematurely for tiny file sizes.

Six 800×480 pages comfortably fit within the device's storage budget in normal formats.

---

# 13. Manifest

Generate something like:

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-31T07:31:12+08:00",
  "source_issue": "https://daily.juya.uk/...",
  "generation_id": "2026-08-31-073112",
  "page_count": 6,
  "pages": [
    {
      "index": 1,
      "url": "pages/page_1.png",
      "sha256": "..."
    },
    {
      "index": 2,
      "url": "pages/page_2.png",
      "sha256": "..."
    }
  ]
}
```

Continue through page 6.

Use relative asset URLs if this simplifies GitHub Pages hosting.

`generation_id` must change only when a new generation is published.

---

# 14. GitHub Actions

Use GitHub Actions for the daily generation.

Target time:

```text
07:30 Asia/Singapore
```

Singapore is UTC+8, therefore standard GitHub cron should be:

```yaml
cron: "30 23 * * *"
```

This means 23:30 UTC on the previous calendar day = 07:30 Singapore time.

Also support:

```yaml
workflow_dispatch:
```

for manual generation.

Workflow roughly:

```text
checkout
↓
setup Python
↓
install dependencies
↓
install Chinese font dependencies if necessary
↓
run backend/generate.py
↓
validate exactly 6 pages
↓
validate manifest
↓
deploy public/ to GitHub Pages
```

Use:

```text
OPENAI_API_KEY
```

from GitHub Secrets.

Optionally:

```text
OPENAI_MODEL
```

from repository variables/secrets.

Do not place secrets in Pages output.

GitHub Actions scheduled workflows can sometimes execute a few minutes late. This is acceptable.

---

# 15. Firmware Framework

Use:

```text
PlatformIO
```

rather than an Arduino IDE-only project.

Use official Seeed-supported E1002 libraries/examples wherever possible.

Before choosing display drivers/libraries, inspect the current:

- Seeed E1002 documentation;
- Seeed GitHub examples;
- PlatformIO examples.

Prefer maintained official implementations rather than writing a custom Spectra 6 driver.

---

# 16. Firmware Configuration

Create:

```text
firmware/include/config.example.h
```

such as:

```cpp
#pragma once

#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"

#define CONTENT_BASE_URL "https://USERNAME.github.io/REPOSITORY/"
```

Actual:

```text
config.h
```

must be gitignored.

Do not ask the user to provide Wi-Fi credentials until the firmware is otherwise ready.

---

# 17. Firmware Boot Behavior

On boot:

```text
initialize serial
↓
initialize buttons
↓
initialize filesystem
↓
initialize display
↓
connect Wi-Fi
↓
fetch manifest
↓
compare generation_id with cached manifest
↓
if new:
    download all six pages
    validate downloads
    atomically replace cached generation
↓
display current page
```

If Wi-Fi/server fails:

```text
use last successfully cached generation
```

Do not blank the screen just because networking fails.

If there is no valid cached content at all, show a simple built-in diagnostic screen.

---

# 18. Download Safety

Never replace the valid cached daily edition page-by-page in place.

Use something equivalent to:

```text
/new/
    page_1
    ...
    page_6
```

Validate:

- all six downloads succeeded;
- expected sizes are plausible;
- hashes match manifest if implemented.

Only then mark the new generation active.

If download fails halfway through, continue using the old complete edition.

---

# 19. Automatic Page Rotation

Required behavior:

```text
Page 1
↓ 10 min
Page 2
↓ 10 min
...
Page 6
↓ 10 min
Page 1
```

Wrap around.

Because e-paper refresh is slow:

- do not start another refresh while one is running;
- measure the ten-minute display interval from after the previous page refresh completes.

A manual page change should reset the automatic ten-minute timer.

Example:

```text
08:00 Page 1
08:04 user presses Right
      → Page 2
08:14 automatic
      → Page 3
```

---

# 20. Buttons

Required only:

```text
LEFT:
previous page

RIGHT:
next page
```

Wrap around:

```text
Page 1 + LEFT  → Page 6
Page 6 + RIGHT → Page 1
```

Middle button does not need a feature.

Do not invent one.

Use proper debounce.

Do not process navigation while an e-paper refresh is already underway.

---

# 21. Daily Content Refresh on Device

The device must eventually notice a newly generated edition without requiring reboot.

Simplest acceptable approach:

- fetch the small `manifest.json` periodically;
- every 10 minutes is acceptable;
- compare only `generation_id`;
- download images only when generation changes.

Therefore a typical page transition can be:

```text
check manifest
↓
new generation?
    yes → download/validate six pages → switch to page 1
    no  → normal next page
```

This is inexpensive because `manifest.json` is tiny.

After receiving a new daily generation:

```text
display Page 1
```

and restart the 10-minute page timer.

---

# 22. Network Failure Behavior

Required:

```text
Manifest request fails:
    keep displaying cached pages

One page download fails:
    abandon new edition
    keep old edition

Wi-Fi unavailable:
    keep displaying cached pages

OpenAI backend fails:
    GitHub Action fails
    do NOT deploy a broken edition
```

A previous valid edition is always preferable to a partially generated new one.

---

# 23. Development Sequence

Implement in this order.

## Phase A — Verify hardware

- identify `/dev/cu.usbserial-110`;
- inspect ESP32 chip;
- inspect flash;
- back up factory firmware;
- verify SHA-256.

No write before backup.

## Phase B — Minimal firmware

Compile a simple firmware capable of:

- serial logs;
- detecting left/right buttons;
- initializing display.

Test buttons without unnecessary repeated panel refreshes.

## Phase C — Static display

Render a local test page:

```text
800×480
3 fake stories
```

Download or embed it and display successfully.

Confirm:

- orientation;
- dimensions;
- Chinese text;
- six-color rendering;
- readable layout.

## Phase D — Network

Firmware fetches:

```text
manifest.json
```

and one test image.

Then all six.

## Phase E — Navigation

Implement:

- six-page cache;
- left/right navigation;
- 10-minute timer;
- wraparound.

## Phase F — Juya backend

Implement reliable Juya extraction.

Save an intermediate JSON during development so parsing can be inspected independently of GPT.

## Phase G — GPT

Implement:

- dynamic categories;
- deduplication;
- summaries;
- exactly 18 results;
- six pages.

## Phase H — GitHub Pages/Actions

Deploy.

Configure device's base URL.

Perform end-to-end test.

---

# 24. Testing

Backend tests should cover at least:

### RSS

- successful current issue;
- newest-issue fallback;
- malformed item;
- missing optional URL;
- duplicate stories.

### GPT output

- valid 18 stories;
- wrong number of stories;
- invalid JSON;
- duplicate IDs;
- excessively long title;
- missing category.

### Rendering

Validate:

```text
width  == 800
height == 480
page count == 6
stories per page == 3
```

No text should escape the page bounds.

### Manifest

Validate:

```text
schema_version
generation_id
page_count == 6
6 valid URLs
```

### Firmware

Test:

```text
page 1 → right → page 2
page 1 → left  → page 6
page 6 → right → page 1

manual change resets timer

network unavailable → cache continues working

new generation → all six download → page 1
partial download failure → old generation remains active
```

---

# 25. Logging

Backend logs should clearly report:

```text
Juya issue selected
number of raw stories
number after parsing
number after GPT deduplication
18 final stories
6 rendered pages
deployment success
```

Firmware serial logs should include:

```text
Wi-Fi state
manifest generation_id
download results
active generation
current page
button events
refresh start/end
errors
```

Do not spam logs continuously.

---

# 26. README

Write a good `README.md` containing:

1. architecture diagram;
2. directory structure;
3. hardware requirements;
4. how factory firmware was backed up;
5. how to restore factory flash;
6. PlatformIO build instructions;
7. firmware flashing instructions;
8. Wi-Fi configuration;
9. OpenAI Secret configuration;
10. GitHub Pages setup;
11. GitHub Actions setup;
12. local backend test;
13. manual workflow run;
14. troubleshooting.

Include exact commands that worked on this MacBook.

---

# 27. Secrets

These files must never be committed:

```text
firmware/include/config.h
.env
backups/
```

Also ignore Python caches, PlatformIO build artifacts, etc.

OpenAI:

```text
OPENAI_API_KEY
```

exists only:

```text
local environment
or
GitHub Actions Secrets
```

Wi-Fi credentials exist only on the local firmware configuration.

---

# 28. Do NOT Build

Do not implement:

- an HTML carousel;
- browser JavaScript auto-pagination;
- SenseCraft RSS layouts;
- a web dashboard;
- weather;
- market information;
- QR codes;
- arbitrary settings menus;
- touch UI;
- middle-button functionality;
- multiple news sources;
- user accounts;
- analytics;
- OTA updates unless absolutely necessary for the initial implementation.

The goal is intentionally narrow.

---

# 29. Final Required User Experience

When finished, the physical device should behave like this:

### Morning

Around 07:30 Singapore time:

```text
GitHub Actions runs
↓
Juya daily issue is processed
↓
18 stories are published
↓
E1002 notices new generation
↓
downloads six pages
↓
shows Page 1
```

### During the day

```text
3 news stories visible
↓
10 minutes
↓
next page
```

Six pages loop continuously.

### Manual

```text
LEFT  → previous page
RIGHT → next page
```

Manual navigation restarts the 10-minute countdown.

---

# 30. Acceptance Criteria

The task is complete only when all of the following are true:

- [ ] factory E1002 flash backed up before custom firmware flashing;
- [ ] backup SHA-256 recorded;
- [ ] PlatformIO firmware compiles;
- [ ] firmware runs on the physical E1002;
- [ ] left/right buttons work;
- [ ] page wraparound works;
- [ ] automatic 10-minute navigation works;
- [ ] manual navigation resets timer;
- [ ] Juya RSS is parsed successfully;
- [ ] OpenAI pipeline outputs exactly 18 real stories;
- [ ] categories are dynamic rather than fixed;
- [ ] exactly six pages are generated;
- [ ] each page contains exactly three stories;
- [ ] pages are exactly 800×480;
- [ ] Chinese text displays clearly;
- [ ] six pages are hosted publicly;
- [ ] manifest is hosted publicly;
- [ ] firmware downloads a new generation;
- [ ] firmware caches pages locally;
- [ ] network loss does not destroy existing content;
- [ ] incomplete new generation does not replace old generation;
- [ ] GitHub Action runs on the 07:30 Singapore schedule;
- [ ] workflow can also be run manually;
- [ ] API key is never committed/exposed;
- [ ] Wi-Fi password is never committed;
- [ ] README documents installation and recovery.

---

# 31. What to Ask the User For

Do as much work as possible first.

When necessary, ask only for the specific remaining human action.

Expected eventual requests are likely:

### Wi-Fi

Ask the user to populate:

```cpp
WIFI_SSID
WIFI_PASSWORD
```

locally.

### OpenAI

Tell the user exactly where to add:

```text
OPENAI_API_KEY
```

as a GitHub Actions Secret.

Do NOT ask the user to paste their API key into chat.

### GitHub

If automatic repository creation/deployment requires authorization that is unavailable, explain exactly what button/action the user must perform.

### Physical ESP32 interaction

If the E1002 must enter bootloader mode manually, tell the user precisely which physical button sequence to perform only at that point.

Otherwise continue autonomously.

---

Proceed with implementation now. Start by inspecting the connected E1002, verifying the serial device, and making the factory flash backup. Do not flash anything until that backup has been successfully verified.