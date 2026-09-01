#pragma once

#include <stdint.h>

inline uint8_t wrapPage(int current, int delta, uint8_t pageCount = 6) {
    if (pageCount == 0) return 0;
    int value = (current + delta) % pageCount;
    return static_cast<uint8_t>(value < 0 ? value + pageCount : value);
}

inline bool pageCountAllowed(int count, uint8_t required, uint8_t maximum, bool allowEmpty) {
    if (count < 0 || count > maximum) return false;
    if (required > 0) return count == required;
    return allowEmpty || count > 0;
}

inline bool intervalElapsed(uint32_t startedAt, uint32_t now, uint32_t interval) {
    return static_cast<uint32_t>(now - startedAt) >= interval;
}

inline bool automaticAdvanceDue(uint32_t startedAt, uint32_t now, uint32_t interval) {
    return interval > 0 && intervalElapsed(startedAt, now, interval);
}

inline uint32_t remainingAfterElapsed(uint32_t remaining, uint32_t elapsed) {
    return elapsed >= remaining ? 0 : remaining - elapsed;
}

inline uint32_t earliestWakeDelay(uint32_t updateRemaining, uint32_t pageRemaining) {
    if (pageRemaining == 0) return updateRemaining;
    return pageRemaining < updateRemaining ? pageRemaining : updateRemaining;
}
