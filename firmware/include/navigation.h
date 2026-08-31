#pragma once

#include <stdint.h>

inline uint8_t wrapPage(int current, int delta, uint8_t pageCount = 6) {
    if (pageCount == 0) return 0;
    int value = (current + delta) % pageCount;
    return static_cast<uint8_t>(value < 0 ? value + pageCount : value);
}

inline bool intervalElapsed(uint32_t startedAt, uint32_t now, uint32_t interval) {
    return static_cast<uint32_t>(now - startedAt) >= interval;
}

inline bool automaticAdvanceDue(uint32_t startedAt, uint32_t now, uint32_t interval) {
    return interval > 0 && intervalElapsed(startedAt, now, interval);
}
