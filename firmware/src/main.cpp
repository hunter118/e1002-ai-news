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
constexpr uint8_t PAGE_COUNT = 6;
constexpr size_t PAGE_BYTES = SCREEN_WIDTH * SCREEN_HEIGHT / 2;
constexpr uint32_t PAGE_INTERVAL_MS = 10UL * 60UL * 1000UL;
constexpr uint32_t WIFI_TIMEOUT_MS = 15000;
constexpr uint32_t HTTP_TIMEOUT_MS = 20000;
constexpr uint32_t DOWNLOAD_STALL_TIMEOUT_MS = 15000;
constexpr uint32_t DEBOUNCE_MS = 50;
constexpr uint8_t PIN_LEFT = 5;   // KEY2, active LOW (official schematic/example)
constexpr uint8_t PIN_MIDDLE = 4; // KEY1, intentionally unused
constexpr uint8_t PIN_RIGHT = 3;  // KEY0, active LOW
constexpr uint8_t PIN_SERIAL_RX = 44;
constexpr uint8_t PIN_SERIAL_TX = 43;

#define LOG Serial1

EPaper display;
Preferences preferences;
bool displayRefreshing = false;
bool filesystemReady = false;
String activeSlot;
String activeGeneration;
uint8_t currentPage = 0;
uint32_t pageIntervalStartedAt = 0;

struct RemotePage {
    String url;
    String sha256;
    size_t size = 0;
};

struct RemoteManifest {
    String generationId;
    String rawJson;
    RemotePage pages[PAGE_COUNT];
};

enum class UpdateResult { Failed, Unchanged, Updated };

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

DebouncedButton leftButton(PIN_LEFT);
DebouncedButton rightButton(PIN_RIGHT);

String slotDirectory(const String &slot) { return "/slot_" + slot; }

String pagePath(const String &slot, uint8_t pageIndex) {
    return slotDirectory(slot) + "/page_" + String(pageIndex + 1) + ".epd";
}

String manifestPath(const String &slot) { return slotDirectory(slot) + "/manifest.json"; }

String resolveUrl(const String &value) {
    if (value.startsWith("https://") || value.startsWith("http://")) return value;
    String base = CONTENT_BASE_URL;
    if (!base.endsWith("/")) base += "/";
    String relative = value;
    while (relative.startsWith("/")) relative.remove(0, 1);
    return base + relative;
}

bool validLocalSlot(const String &slot, String &generation) {
    if (!filesystemReady || (slot != "a" && slot != "b")) return false;
    File manifestFile = LittleFS.open(manifestPath(slot), "r");
    if (!manifestFile) return false;
    JsonDocument document;
    const DeserializationError error = deserializeJson(document, manifestFile);
    manifestFile.close();
    if (error || document["schema_version"].as<int>() != 1 || document["page_count"].as<int>() != PAGE_COUNT) {
        return false;
    }
    const char *generationValue = document["generation_id"] | "";
    if (!generationValue[0]) return false;
    for (uint8_t index = 0; index < PAGE_COUNT; ++index) {
        File page = LittleFS.open(pagePath(slot, index), "r");
        if (!page || page.size() != PAGE_BYTES) {
            if (page) page.close();
            return false;
        }
        page.close();
    }
    generation = generationValue;
    return true;
}

bool loadCachedGeneration() {
    String preferred = preferences.getString("active_slot", "a");
    String generation;
    if (validLocalSlot(preferred, generation)) {
        activeSlot = preferred;
        activeGeneration = generation;
        return true;
    }
    String alternate = preferred == "a" ? "b" : "a";
    if (validLocalSlot(alternate, generation)) {
        activeSlot = alternate;
        activeGeneration = generation;
        preferences.putString("active_slot", alternate);
        return true;
    }
    activeSlot = "";
    activeGeneration = "";
    return false;
}

bool connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) return true;
    LOG.printf("[wifi] connecting to %s\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    const uint32_t started = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - started < WIFI_TIMEOUT_MS) {
        delay(250);
    }
    if (WiFi.status() != WL_CONNECTED) {
        LOG.printf("[wifi] connection failed, status=%d\n", static_cast<int>(WiFi.status()));
        return false;
    }
    LOG.printf("[wifi] connected, IP=%s\n", WiFi.localIP().toString().c_str());
    return true;
}

bool parseManifest(const String &payload, RemoteManifest &manifest) {
    JsonDocument document;
    const DeserializationError error = deserializeJson(document, payload);
    if (error) {
        LOG.printf("[manifest] JSON error: %s\n", error.c_str());
        return false;
    }
    if (document["schema_version"].as<int>() != 1 || document["page_count"].as<int>() != PAGE_COUNT) {
        LOG.println("[manifest] unsupported schema or page count");
        return false;
    }
    JsonArray pages = document["pages"].as<JsonArray>();
    const char *generation = document["generation_id"] | "";
    if (!generation[0] || pages.size() != PAGE_COUNT) return false;
    manifest.generationId = generation;
    manifest.rawJson = payload;
    for (uint8_t index = 0; index < PAGE_COUNT; ++index) {
        JsonObject page = pages[index].as<JsonObject>();
        const char *url = page["url"] | "";
        const char *sha = page["sha256"] | "";
        const size_t size = page["size"] | 0;
        if (page["index"].as<int>() != index + 1 || !url[0] || strlen(sha) != 64 || size != PAGE_BYTES) {
            LOG.printf("[manifest] invalid page entry %u\n", index + 1);
            return false;
        }
        manifest.pages[index].url = resolveUrl(url);
        manifest.pages[index].sha256 = sha;
        manifest.pages[index].size = size;
    }
    LOG.printf("[manifest] generation_id=%s\n", manifest.generationId.c_str());
    return true;
}

bool fetchManifest(RemoteManifest &manifest) {
    WiFiClientSecure client;
    // GitHub Pages serves public, non-sensitive pixels. SHA-256 detects corrupted or
    // incomplete page transfers; the firmware never handles API keys or private data.
    client.setInsecure();
    HTTPClient http;
    const String url = resolveUrl("manifest.json");
    http.setTimeout(HTTP_TIMEOUT_MS);
    if (!http.begin(client, url)) return false;
    const int status = http.GET();
    if (status != HTTP_CODE_OK) {
        LOG.printf("[manifest] GET failed: HTTP %d\n", status);
        http.end();
        return false;
    }
    const String payload = http.getString();
    http.end();
    return parseManifest(payload, manifest);
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

    mbedtls_sha256_context shaContext;
    mbedtls_sha256_init(&shaContext);
    mbedtls_sha256_starts_ret(&shaContext, 0);
    WiFiClient *stream = http.getStreamPtr();
    uint8_t buffer[4096];
    size_t total = 0;
    uint32_t lastProgress = millis();
    bool ok = true;
    while (total < remote.size) {
        const int available = stream->available();
        if (available > 0) {
            const size_t wanted = min(static_cast<size_t>(available), min(sizeof(buffer), remote.size - total));
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

void clearSlot(const String &slot) {
    LittleFS.mkdir(slotDirectory(slot));
    for (uint8_t index = 0; index < PAGE_COUNT; ++index) {
        LittleFS.remove(pagePath(slot, index));
        LittleFS.remove(pagePath(slot, index) + ".tmp");
    }
    LittleFS.remove(manifestPath(slot));
}

bool stageAndActivate(const RemoteManifest &manifest) {
    const String targetSlot = activeSlot == "a" ? "b" : "a";
    LOG.printf("[cache] staging generation %s in slot %s\n", manifest.generationId.c_str(), targetSlot.c_str());
    clearSlot(targetSlot);
    for (uint8_t index = 0; index < PAGE_COUNT; ++index) {
        if (!downloadPage(manifest.pages[index], pagePath(targetSlot, index))) {
            LOG.printf("[cache] page %u failed; keeping generation %s\n", index + 1, activeGeneration.c_str());
            clearSlot(targetSlot);
            return false;
        }
    }
    File localManifest = LittleFS.open(manifestPath(targetSlot), "w");
    if (!localManifest || localManifest.print(manifest.rawJson) != manifest.rawJson.length()) {
        if (localManifest) localManifest.close();
        clearSlot(targetSlot);
        return false;
    }
    localManifest.flush();
    localManifest.close();
    String validatedGeneration;
    if (!validLocalSlot(targetSlot, validatedGeneration) || validatedGeneration != manifest.generationId) {
        clearSlot(targetSlot);
        return false;
    }

    // NVS is the atomic pointer: the old complete slot remains untouched until here.
    preferences.putString("active_slot", targetSlot);
    activeSlot = targetSlot;
    activeGeneration = manifest.generationId;
    currentPage = 0;
    LOG.printf("[cache] active generation=%s slot=%s\n", activeGeneration.c_str(), activeSlot.c_str());
    return true;
}

UpdateResult checkForUpdate() {
    if (!filesystemReady || !connectWiFi()) return UpdateResult::Failed;
    RemoteManifest manifest;
    if (!fetchManifest(manifest)) return UpdateResult::Failed;
    if (!activeGeneration.isEmpty() && manifest.generationId == activeGeneration) {
        return UpdateResult::Unchanged;
    }
    return stageAndActivate(manifest) ? UpdateResult::Updated : UpdateResult::Failed;
}

bool displayPage(uint8_t pageIndex) {
    if (displayRefreshing || activeSlot.isEmpty() || pageIndex >= PAGE_COUNT) return false;
    const String path = pagePath(activeSlot, pageIndex);
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
    LOG.printf("[display] refresh start page=%u generation=%s\n", pageIndex + 1, activeGeneration.c_str());
    display.pushImage(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, reinterpret_cast<uint16_t *>(pixels));
    display.update();
    free(pixels);
    currentPage = pageIndex;
    pageIntervalStartedAt = millis(); // Ten minutes start after the slow refresh completes.
    displayRefreshing = false;
    LOG.printf("[display] refresh end page=%u\n", currentPage + 1);
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
    pageIntervalStartedAt = millis();
    displayRefreshing = false;
}

void navigate(int delta, const char *reason) {
    if (displayRefreshing || activeSlot.isEmpty()) return;
    const uint8_t target = wrapPage(currentPage, delta, PAGE_COUNT);
    LOG.printf("[button] %s -> page %u\n", reason, target + 1);
    displayPage(target);
}

} // namespace

void setup() {
    LOG.begin(115200, SERIAL_8N1, PIN_SERIAL_RX, PIN_SERIAL_TX);
    delay(500);
    LOG.println("[boot] reTerminal E1002 AI Daily starting");
    leftButton.begin();
    rightButton.begin();
    pinMode(PIN_MIDDLE, INPUT); // Deliberately no feature.

    display.begin();
    filesystemReady = LittleFS.begin(false);
    if (!filesystemReady) {
        // A brand-new custom partition is unformatted. The format-on-failure path is
        // logged and only runs when no readable cache exists.
        LOG.println("[fs] LittleFS mount failed; formatting the new/unreadable cache partition");
        filesystemReady = LittleFS.begin(true);
        if (!filesystemReady) {
            showDiagnostic("CACHE ERROR", "LittleFS mount failed");
            return;
        }
    }
    LittleFS.mkdir("/slot_a");
    LittleFS.mkdir("/slot_b");
    preferences.begin("ai-news", false);
    const bool hadCache = loadCachedGeneration();
    LOG.printf("[cache] boot cache=%s generation=%s\n", hadCache ? "valid" : "none", activeGeneration.c_str());

    const UpdateResult update = checkForUpdate();
    if (!activeSlot.isEmpty()) {
        currentPage = 0;
        displayPage(currentPage);
    } else {
        LOG.println("[boot] no valid cache and no downloadable edition");
        showDiagnostic("AI DAILY OFFLINE", "No valid cached edition");
    }
    if (update == UpdateResult::Failed && hadCache) {
        LOG.println("[network] update failed; continuing with cached edition");
    }
}

void loop() {
    if (!filesystemReady) {
        delay(1000);
        return;
    }
    if (leftButton.pressedEvent()) navigate(-1, "LEFT");
    if (rightButton.pressedEvent()) navigate(1, "RIGHT");

    if (!displayRefreshing && intervalElapsed(pageIntervalStartedAt, millis(), PAGE_INTERVAL_MS)) {
        const UpdateResult update = checkForUpdate();
        if (update == UpdateResult::Updated) {
            displayPage(0);
        } else if (!activeSlot.isEmpty()) {
            // A failed manifest request must not stop the cached page rotation.
            displayPage(wrapPage(currentPage, 1, PAGE_COUNT));
        } else {
            // First boot may have had no network. Keep retrying without requiring reboot.
            pageIntervalStartedAt = millis();
        }
    }
    delay(10);
}
