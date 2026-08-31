#include <Arduino.h>
#include <ArduinoJson.h>
#include <FS.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <mbedtls/sha256.h>

#include "config.h"
#include "driver.h"
#include "navigation.h"
#include "TFT_eSPI.h"

#ifndef EPAPER_ENABLE
#error "BOARD_SCREEN_COMBO 521 did not enable the E1002 EPaper driver"
#endif

namespace {

constexpr uint16_t SCREEN_WIDTH = 800;
constexpr uint16_t SCREEN_HEIGHT = 480;
constexpr size_t PAGE_BYTES = SCREEN_WIDTH * SCREEN_HEIGHT / 2;
constexpr uint8_t MAX_PAGE_COUNT = 20;
constexpr uint32_t NEWS_INTERVAL_MS = 10UL * 60UL * 1000UL;
constexpr uint32_t GALLERY_INTERVAL_MS = 30UL * 1000UL;
constexpr uint32_t MIN_INTERVAL_MS = 10UL * 1000UL;
constexpr uint32_t MAX_INTERVAL_MS = 24UL * 60UL * 60UL * 1000UL;
constexpr uint32_t UPDATE_CHECK_MS = 10UL * 60UL * 1000UL;
constexpr uint32_t WIFI_TIMEOUT_MS = 15000;
constexpr uint32_t HTTP_TIMEOUT_MS = 20000;
constexpr uint32_t DOWNLOAD_STALL_TIMEOUT_MS = 15000;
constexpr size_t DOWNLOAD_BUFFER_BYTES = 4096;
constexpr uint32_t DEBOUNCE_MS = 50;
constexpr uint8_t PIN_PREVIOUS = 5; // Physical left, KEY2, active LOW.
constexpr uint8_t PIN_NEXT = 4;     // Physical middle, KEY1, active LOW.
constexpr uint8_t PIN_MODE = 3;     // Physical right, KEY0, active LOW.
constexpr uint8_t PIN_SERIAL_RX = 44;
constexpr uint8_t PIN_SERIAL_TX = 43;
constexpr const char *LITTLEFS_PARTITION_LABEL = "littlefs";

#define LOG Serial1

enum class DisplayMode : uint8_t { News, Gallery };
enum class UpdateResult : uint8_t { Failed, Unchanged, Updated };

struct RemotePage {
    String url;
    String sha256;
    size_t size = 0;
};

struct RemoteManifest {
    String generationId;
    String rawJson;
    uint8_t pageCount = 0;
    uint32_t intervalMs = 0;
    RemotePage pages[MAX_PAGE_COUNT];
};

struct ModeState {
    ModeState(const char *displayLabelValue,
              const char *directoryPrefixValue,
              const char *baseUrlValue,
              const char *manifestRelativeValue,
              const char *preferenceKeyValue,
              uint8_t requiredPageCountValue,
              bool allowEmptyValue,
              uint32_t defaultIntervalValue)
        : displayLabel(displayLabelValue),
          directoryPrefix(directoryPrefixValue),
          baseUrl(baseUrlValue),
          manifestRelative(manifestRelativeValue),
          preferenceKey(preferenceKeyValue),
          requiredPageCount(requiredPageCountValue),
          allowEmpty(allowEmptyValue),
          defaultIntervalMs(defaultIntervalValue),
          intervalMs(defaultIntervalValue) {}

    const char *displayLabel;
    const char *directoryPrefix;
    const char *baseUrl;
    const char *manifestRelative;
    const char *preferenceKey;
    uint8_t requiredPageCount;
    bool allowEmpty;
    uint32_t defaultIntervalMs;
    String activeSlot;
    String activeGeneration;
    uint8_t pageCount = 0;
    uint8_t currentPage = 0;
    uint32_t intervalMs;
    uint32_t intervalStartedAt = 0;
};

class DebouncedButton {
public:
    explicit DebouncedButton(uint8_t pin) : pin_(pin) {}

    void begin() {
        pinMode(pin_, INPUT); // E1002 board already provides hardware pull-ups.
        stable_ = candidate_ = digitalRead(pin_);
        changedAt_ = millis();
    }

    bool pressedEvent() {
        const bool raw = digitalRead(pin_);
        const uint32_t now = millis();
        if (raw != candidate_) {
            candidate_ = raw;
            changedAt_ = now;
        }
        if (candidate_ != stable_ && now - changedAt_ >= DEBOUNCE_MS) {
            stable_ = candidate_;
            return stable_ == LOW;
        }
        return false;
    }

private:
    uint8_t pin_;
    bool stable_ = HIGH;
    bool candidate_ = HIGH;
    uint32_t changedAt_ = 0;
};

EPaper display;
Preferences preferences;
bool displayRefreshing = false;
bool filesystemReady = false;
DisplayMode currentMode = DisplayMode::News;
uint32_t lastUpdateCheckAt = 0;
ModeState newsState("NEWS", "news", NEWS_BASE_URL, "manifest.json", "news_slot", 6, false, NEWS_INTERVAL_MS);
ModeState galleryState(
    "PHOTO ALBUM", "photo", GALLERY_BASE_URL, "api/gallery/manifest", "photo_slot", 0, true, GALLERY_INTERVAL_MS);
DebouncedButton previousButton(PIN_PREVIOUS);
DebouncedButton nextButton(PIN_NEXT);
DebouncedButton modeButton(PIN_MODE);

ModeState &activeState() { return currentMode == DisplayMode::News ? newsState : galleryState; }

String slotDirectory(const ModeState &state, const String &slot) {
    return "/" + String(state.directoryPrefix) + "_" + slot;
}

String pagePath(const ModeState &state, const String &slot, uint8_t pageIndex) {
    return slotDirectory(state, slot) + "/page_" + String(pageIndex + 1) + ".epd";
}

String manifestPath(const ModeState &state, const String &slot) {
    return slotDirectory(state, slot) + "/manifest.json";
}

String resolveUrl(const char *baseUrlValue, const String &value) {
    if (value.startsWith("https://") || value.startsWith("http://")) return value;
    String base = baseUrlValue;
    if (!base.endsWith("/")) base += "/";
    String relative = value;
    while (relative.startsWith("/")) relative.remove(0, 1);
    return base + relative;
}

bool validPageCount(const ModeState &state, int count) {
    if (count < 0 || count > MAX_PAGE_COUNT) return false;
    if (state.requiredPageCount > 0) return count == state.requiredPageCount;
    return state.allowEmpty ? true : count > 0;
}

uint32_t validInterval(uint32_t value, uint32_t fallback) {
    return value >= MIN_INTERVAL_MS && value <= MAX_INTERVAL_MS ? value : fallback;
}

bool validLocalSlot(
    ModeState &state, const String &slot, String &generation, uint8_t &pageCount, uint32_t &intervalMs) {
    if (!filesystemReady || (slot != "a" && slot != "b")) return false;
    File manifestFile = LittleFS.open(manifestPath(state, slot), "r");
    if (!manifestFile) return false;
    JsonDocument document;
    const DeserializationError error = deserializeJson(document, manifestFile);
    manifestFile.close();
    const int count = document["page_count"] | -1;
    if (error || document["schema_version"].as<int>() != 1 || !validPageCount(state, count)) return false;
    const char *generationValue = document["generation_id"] | "";
    if (!generationValue[0]) return false;
    for (int index = 0; index < count; ++index) {
        File page = LittleFS.open(pagePath(state, slot, static_cast<uint8_t>(index)), "r");
        if (!page || page.size() != PAGE_BYTES) {
            if (page) page.close();
            return false;
        }
        page.close();
    }
    generation = generationValue;
    pageCount = static_cast<uint8_t>(count);
    intervalMs = validInterval(document["interval_ms"] | state.defaultIntervalMs, state.defaultIntervalMs);
    return true;
}

bool loadCachedGeneration(ModeState &state) {
    const String preferred = preferences.getString(state.preferenceKey, "a");
    String generation;
    uint8_t pageCount = 0;
    uint32_t intervalMs = state.defaultIntervalMs;
    if (validLocalSlot(state, preferred, generation, pageCount, intervalMs)) {
        state.activeSlot = preferred;
    } else {
        const String alternate = preferred == "a" ? "b" : "a";
        if (!validLocalSlot(state, alternate, generation, pageCount, intervalMs)) {
            state.activeSlot = "";
            state.activeGeneration = "";
            state.pageCount = 0;
            state.intervalMs = state.defaultIntervalMs;
            return false;
        }
        state.activeSlot = alternate;
        preferences.putString(state.preferenceKey, alternate);
    }
    state.activeGeneration = generation;
    state.pageCount = pageCount;
    state.intervalMs = intervalMs;
    state.currentPage = pageCount == 0 ? 0 : min(state.currentPage, static_cast<uint8_t>(pageCount - 1));
    return true;
}

bool connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) return true;
    LOG.printf("[wifi] connecting to %s\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    const uint32_t started = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - started < WIFI_TIMEOUT_MS) delay(250);
    if (WiFi.status() != WL_CONNECTED) {
        LOG.printf("[wifi] connection failed, status=%d\n", static_cast<int>(WiFi.status()));
        return false;
    }
    LOG.printf("[wifi] connected, IP=%s\n", WiFi.localIP().toString().c_str());
    return true;
}

bool parseManifest(const String &payload, ModeState &state, RemoteManifest &manifest) {
    JsonDocument document;
    const DeserializationError error = deserializeJson(document, payload);
    if (error) {
        LOG.printf("[%s] manifest JSON error: %s\n", state.directoryPrefix, error.c_str());
        return false;
    }
    const int count = document["page_count"] | -1;
    if (document["schema_version"].as<int>() != 1 || !validPageCount(state, count)) {
        LOG.printf("[%s] unsupported schema/page count=%d\n", state.directoryPrefix, count);
        return false;
    }
    JsonArray pages = document["pages"].as<JsonArray>();
    const char *generation = document["generation_id"] | "";
    if (!generation[0] || pages.size() != static_cast<size_t>(count)) return false;
    manifest.generationId = generation;
    manifest.rawJson = payload;
    manifest.pageCount = static_cast<uint8_t>(count);
    manifest.intervalMs = validInterval(document["interval_ms"] | state.defaultIntervalMs, state.defaultIntervalMs);
    for (int index = 0; index < count; ++index) {
        JsonObject page = pages[index].as<JsonObject>();
        const char *url = page["url"] | "";
        const char *sha = page["sha256"] | "";
        const size_t size = page["size"] | 0;
        if (page["index"].as<int>() != index + 1 || !url[0] || strlen(sha) != 64 || size != PAGE_BYTES) {
            LOG.printf("[%s] invalid page entry %d\n", state.directoryPrefix, index + 1);
            return false;
        }
        manifest.pages[index].url = resolveUrl(state.baseUrl, url);
        manifest.pages[index].sha256 = sha;
        manifest.pages[index].size = size;
    }
    LOG.printf("[%s] generation=%s pages=%u interval=%u ms\n",
               state.directoryPrefix,
               manifest.generationId.c_str(),
               manifest.pageCount,
               static_cast<unsigned>(manifest.intervalMs));
    return true;
}

bool fetchManifest(ModeState &state, RemoteManifest &manifest) {
    WiFiClientSecure client;
    // Both endpoints serve public, non-sensitive pixels. SHA-256 protects each
    // cached device page against incomplete or corrupted transfers.
    client.setInsecure();
    HTTPClient http;
    const String url = resolveUrl(state.baseUrl, state.manifestRelative);
    http.setTimeout(HTTP_TIMEOUT_MS);
    if (!http.begin(client, url)) return false;
    const int status = http.GET();
    if (status != HTTP_CODE_OK) {
        LOG.printf("[%s] manifest HTTP %d\n", state.directoryPrefix, status);
        http.end();
        return false;
    }
    const String payload = http.getString();
    http.end();
    return parseManifest(payload, state, manifest);
}

String hexDigest(const uint8_t digest[32]) {
    static const char hex[] = "0123456789abcdef";
    char output[65];
    for (uint8_t index = 0; index < 32; ++index) {
        output[index * 2] = hex[digest[index] >> 4];
        output[index * 2 + 1] = hex[digest[index] & 0x0F];
    }
    output[64] = '\0';
    return String(output);
}

bool downloadPage(const RemotePage &remote, const String &destination) {
    LOG.printf("[download] starting %s -> %s\n", remote.url.c_str(), destination.c_str());
    const String temporary = destination + ".tmp";
    LittleFS.remove(temporary);
    File output = LittleFS.open(temporary, "w");
    if (!output) {
        LOG.printf("[download] cannot open %s\n", temporary.c_str());
        return false;
    }
    WiFiClientSecure client;
    client.setInsecure();
    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    if (!http.begin(client, remote.url)) {
        output.close();
        LittleFS.remove(temporary);
        return false;
    }
    const int status = http.GET();
    if (status != HTTP_CODE_OK) {
        LOG.printf("[download] %s HTTP %d\n", remote.url.c_str(), status);
        http.end();
        output.close();
        LittleFS.remove(temporary);
        return false;
    }
    uint8_t *buffer = static_cast<uint8_t *>(ps_malloc(DOWNLOAD_BUFFER_BYTES));
    if (!buffer) buffer = static_cast<uint8_t *>(malloc(DOWNLOAD_BUFFER_BYTES));
    if (!buffer) {
        LOG.println("[download] cannot allocate transfer buffer");
        http.end();
        output.close();
        LittleFS.remove(temporary);
        return false;
    }
    mbedtls_sha256_context shaContext;
    mbedtls_sha256_init(&shaContext);
    mbedtls_sha256_starts_ret(&shaContext, 0);
    WiFiClient *stream = http.getStreamPtr();
    size_t total = 0;
    uint32_t lastProgress = millis();
    bool ok = true;
    while (total < remote.size) {
        const int available = stream->available();
        if (available > 0) {
            const size_t wanted = min(
                static_cast<size_t>(available), min(DOWNLOAD_BUFFER_BYTES, remote.size - total));
            const int count = stream->readBytes(buffer, wanted);
            if (count <= 0 || output.write(buffer, count) != static_cast<size_t>(count)) {
                ok = false;
                break;
            }
            mbedtls_sha256_update_ret(&shaContext, buffer, count);
            total += count;
            lastProgress = millis();
        } else if (!http.connected() || millis() - lastProgress > DOWNLOAD_STALL_TIMEOUT_MS) {
            ok = false;
            break;
        } else {
            delay(2);
        }
    }
    uint8_t digest[32];
    mbedtls_sha256_finish_ret(&shaContext, digest);
    mbedtls_sha256_free(&shaContext);
    free(buffer);
    http.end();
    output.flush();
    output.close();
    const String actualHash = hexDigest(digest);
    if (!ok || total != remote.size || !actualHash.equalsIgnoreCase(remote.sha256)) {
        LOG.printf("[download] validation failed bytes=%u/%u sha=%s\n",
                   static_cast<unsigned>(total), static_cast<unsigned>(remote.size), actualHash.c_str());
        LittleFS.remove(temporary);
        return false;
    }
    LittleFS.remove(destination);
    if (!LittleFS.rename(temporary, destination)) {
        LittleFS.remove(temporary);
        return false;
    }
    LOG.printf("[download] validated %s (%u bytes)\n", destination.c_str(), static_cast<unsigned>(total));
    return true;
}

void clearSlot(ModeState &state, const String &slot) {
    LittleFS.mkdir(slotDirectory(state, slot));
    for (uint8_t index = 0; index < MAX_PAGE_COUNT; ++index) {
        LittleFS.remove(pagePath(state, slot, index));
        LittleFS.remove(pagePath(state, slot, index) + ".tmp");
    }
    LittleFS.remove(manifestPath(state, slot));
}

bool stageAndActivate(ModeState &state, const RemoteManifest &manifest) {
    const String targetSlot = state.activeSlot == "a" ? "b" : "a";
    LOG.printf("[%s] staging generation %s in slot %s\n",
               state.directoryPrefix, manifest.generationId.c_str(), targetSlot.c_str());
    clearSlot(state, targetSlot);
    for (uint8_t index = 0; index < manifest.pageCount; ++index) {
        if (!downloadPage(manifest.pages[index], pagePath(state, targetSlot, index))) {
            LOG.printf("[%s] page %u failed; preserving generation %s\n",
                       state.directoryPrefix, index + 1, state.activeGeneration.c_str());
            clearSlot(state, targetSlot);
            return false;
        }
    }
    File localManifest = LittleFS.open(manifestPath(state, targetSlot), "w");
    if (!localManifest || localManifest.print(manifest.rawJson) != manifest.rawJson.length()) {
        if (localManifest) localManifest.close();
        clearSlot(state, targetSlot);
        return false;
    }
    localManifest.flush();
    localManifest.close();
    String validatedGeneration;
    uint8_t validatedCount = 0;
    uint32_t validatedInterval = state.defaultIntervalMs;
    if (!validLocalSlot(state, targetSlot, validatedGeneration, validatedCount, validatedInterval) ||
        validatedGeneration != manifest.generationId) {
        clearSlot(state, targetSlot);
        return false;
    }
    preferences.putString(state.preferenceKey, targetSlot);
    state.activeSlot = targetSlot;
    state.activeGeneration = manifest.generationId;
    state.pageCount = validatedCount;
    state.intervalMs = validatedInterval;
    state.currentPage = 0;
    LOG.printf("[%s] active generation=%s slot=%s pages=%u\n",
               state.directoryPrefix, state.activeGeneration.c_str(), state.activeSlot.c_str(), state.pageCount);
    return true;
}

UpdateResult checkForUpdate(ModeState &state) {
    if (!filesystemReady || !connectWiFi()) return UpdateResult::Failed;
    RemoteManifest manifest;
    if (!fetchManifest(state, manifest)) return UpdateResult::Failed;
    if (!state.activeGeneration.isEmpty() && manifest.generationId == state.activeGeneration) {
        state.intervalMs = manifest.intervalMs;
        return UpdateResult::Unchanged;
    }
    return stageAndActivate(state, manifest) ? UpdateResult::Updated : UpdateResult::Failed;
}

bool displayPage(ModeState &state, uint8_t pageIndex) {
    if (displayRefreshing || state.activeSlot.isEmpty() || pageIndex >= state.pageCount) return false;
    const String path = pagePath(state, state.activeSlot, pageIndex);
    File page = LittleFS.open(path, "r");
    if (!page || page.size() != PAGE_BYTES) {
        LOG.printf("[display] invalid cached page %s\n", path.c_str());
        if (page) page.close();
        return false;
    }
    uint8_t *pixels = static_cast<uint8_t *>(ps_malloc(PAGE_BYTES));
    if (!pixels) pixels = static_cast<uint8_t *>(malloc(PAGE_BYTES));
    if (!pixels || page.read(pixels, PAGE_BYTES) != PAGE_BYTES) {
        if (pixels) free(pixels);
        page.close();
        return false;
    }
    page.close();
    displayRefreshing = true;
    LOG.printf("[display] %s refresh start page=%u/%u generation=%s\n",
               state.directoryPrefix, pageIndex + 1, state.pageCount, state.activeGeneration.c_str());
    display.pushImage(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, reinterpret_cast<uint16_t *>(pixels));
    display.update();
    free(pixels);
    state.currentPage = pageIndex;
    state.intervalStartedAt = millis(); // Restart after the slow physical refresh completes.
    displayRefreshing = false;
    LOG.printf("[display] %s refresh end page=%u/%u\n", state.directoryPrefix, pageIndex + 1, state.pageCount);
    return true;
}

void showDiagnostic(const char *line1, const char *line2) {
    if (displayRefreshing) return;
    displayRefreshing = true;
    display.fillScreen(TFT_WHITE);
    display.setTextColor(TFT_BLACK, TFT_WHITE);
    display.setTextSize(3);
    display.setCursor(55, 170);
    display.print(line1);
    display.setTextSize(2);
    display.setCursor(55, 235);
    display.print(line2);
    display.update();
    activeState().intervalStartedAt = millis();
    displayRefreshing = false;
}

void showActiveMode() {
    ModeState &state = activeState();
    if (state.pageCount > 0 && !state.activeSlot.isEmpty()) {
        if (state.currentPage >= state.pageCount) state.currentPage = 0;
        displayPage(state, state.currentPage);
    } else if (currentMode == DisplayMode::Gallery) {
        showDiagnostic("PHOTO ALBUM", "No photos yet");
    } else {
        showDiagnostic("AI DAILY OFFLINE", "No valid cached edition");
    }
}

void navigate(int delta, const char *reason) {
    ModeState &state = activeState();
    if (displayRefreshing || state.pageCount == 0 || state.activeSlot.isEmpty()) return;
    const uint8_t target = wrapPage(state.currentPage, delta, state.pageCount);
    LOG.printf("[button] %s mode=%s page=%u->%u\n",
               reason, state.directoryPrefix, state.currentPage + 1, target + 1);
    displayPage(state, target);
}

void switchMode() {
    if (displayRefreshing) return;
    currentMode = currentMode == DisplayMode::News ? DisplayMode::Gallery : DisplayMode::News;
    LOG.printf("[button] mode -> %s\n", activeState().directoryPrefix);
    showActiveMode();
}

void refreshUpdates() {
    const UpdateResult news = checkForUpdate(newsState);
    const UpdateResult gallery = checkForUpdate(galleryState);
    ModeState &state = activeState();
    const bool activeUpdated =
        (currentMode == DisplayMode::News && news == UpdateResult::Updated) ||
        (currentMode == DisplayMode::Gallery && gallery == UpdateResult::Updated);
    if (activeUpdated) showActiveMode();
    state.intervalStartedAt = millis();
}

} // namespace

void setup() {
    LOG.begin(115200, SERIAL_8N1, PIN_SERIAL_RX, PIN_SERIAL_TX);
    delay(500);
    LOG.println("[boot] reTerminal E1002 AI Daily + Gallery starting");
    previousButton.begin();
    nextButton.begin();
    modeButton.begin();
    display.begin();
    filesystemReady = LittleFS.begin(false, "/littlefs", 10, LITTLEFS_PARTITION_LABEL);
    if (!filesystemReady) {
        LOG.println("[fs] LittleFS mount failed; formatting the new/unreadable cache partition");
        filesystemReady = LittleFS.begin(true, "/littlefs", 10, LITTLEFS_PARTITION_LABEL);
        if (!filesystemReady) {
            LOG.println("[fs] LittleFS format/mount failed");
            showDiagnostic("CACHE ERROR", "LittleFS mount failed");
            return;
        }
    }
    LOG.printf("[fs] LittleFS ready total=%u used=%u\n",
               static_cast<unsigned>(LittleFS.totalBytes()), static_cast<unsigned>(LittleFS.usedBytes()));
    preferences.begin("ai-news", false);
    const bool newsCache = loadCachedGeneration(newsState);
    const bool galleryCache = loadCachedGeneration(galleryState);
    LOG.printf("[cache] news=%s generation=%s pages=%u; gallery=%s generation=%s pages=%u\n",
               newsCache ? "valid" : "none", newsState.activeGeneration.c_str(), newsState.pageCount,
               galleryCache ? "valid" : "none", galleryState.activeGeneration.c_str(), galleryState.pageCount);
    checkForUpdate(newsState);
    checkForUpdate(galleryState);
    currentMode = DisplayMode::News;
    showActiveMode();
    lastUpdateCheckAt = millis();
}

void loop() {
    if (previousButton.pressedEvent()) navigate(-1, "previous");
    if (nextButton.pressedEvent()) navigate(1, "next");
    if (modeButton.pressedEvent()) switchMode();

    const uint32_t now = millis();
    ModeState &state = activeState();
    if (!displayRefreshing && state.pageCount > 0 &&
        intervalElapsed(state.intervalStartedAt, now, state.intervalMs)) {
        LOG.printf("[timer] %s automatic next page\n", state.directoryPrefix);
        navigate(1, "automatic");
    }
    if (!displayRefreshing && intervalElapsed(lastUpdateCheckAt, now, UPDATE_CHECK_MS)) {
        lastUpdateCheckAt = now;
        refreshUpdates();
    }
    delay(5);
}
