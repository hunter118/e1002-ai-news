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

    assert(!intervalElapsed(1000, 1000 + 599999, 600000));
    assert(intervalElapsed(1000, 1000 + 600000, 600000));
    // Unsigned subtraction keeps the timer correct across millis() rollover.
    assert(intervalElapsed(UINT32_MAX - 100, 100, 150));
    return 0;
}
