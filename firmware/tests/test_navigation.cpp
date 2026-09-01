#include <cassert>
#include <cstdint>

#include "navigation.h"

int main() {
    assert(wrapPage(0, 1) == 1);  // page 1 -> next -> page 2
    assert(wrapPage(0, -1) == 5); // page 1 -> previous -> page 6
    assert(wrapPage(5, 1) == 0);  // page 6 -> next -> page 1
    assert(wrapPage(5, -1) == 4);

    // Gallery page counts are dynamic, including an empty album.
    assert(wrapPage(0, -1, 3) == 2);
    assert(wrapPage(2, 1, 3) == 0);
    assert(wrapPage(0, 1, 0) == 0);

    // News accepts a dynamic one-to-six-page edition; the gallery allows zero to twenty.
    assert(pageCountAllowed(1, 0, 6, false));
    assert(pageCountAllowed(6, 0, 6, false));
    assert(!pageCountAllowed(0, 0, 6, false));
    assert(!pageCountAllowed(7, 0, 6, false));
    assert(pageCountAllowed(0, 0, 20, true));
    assert(pageCountAllowed(20, 0, 20, true));

    assert(!intervalElapsed(1000, 1000 + 599999, 600000));
    assert(intervalElapsed(1000, 1000 + 600000, 600000));
    // Unsigned subtraction keeps the timer correct across millis() rollover.
    assert(intervalElapsed(UINT32_MAX - 100, 100, 150));
    assert(!automaticAdvanceDue(1000, UINT32_MAX, 0));
    assert(!automaticAdvanceDue(1000, 1999, 1000));
    assert(automaticAdvanceDue(1000, 2000, 1000));

    assert(remainingAfterElapsed(600000, 100000) == 500000);
    assert(remainingAfterElapsed(600000, 600000) == 0);
    assert(remainingAfterElapsed(600000, 700000) == 0);
    assert(earliestWakeDelay(3600000, 600000) == 600000);
    assert(earliestWakeDelay(3600000, 0) == 3600000);
    return 0;
}
