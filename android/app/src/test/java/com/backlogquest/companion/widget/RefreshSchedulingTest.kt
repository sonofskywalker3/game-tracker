package com.backlogquest.companion.widget

import org.junit.Assert.*
import org.junit.Test

class RefreshSchedulingTest {
    @Test fun shouldFetch_whenNeverFetched() {
        assertTrue(shouldFetch(lastSavedMillis = null, nowMillis = 1_000L))
    }

    @Test fun shouldFetch_whenStale() {
        val now = 100_000_000L
        assertTrue(shouldFetch(lastSavedMillis = now - (FETCH_STALE_MILLIS + 1), nowMillis = now))
    }

    @Test fun shouldNotFetch_whenFresh() {
        val now = 100_000_000L
        assertFalse(shouldFetch(lastSavedMillis = now - 1_000L, nowMillis = now))
    }

    @Test fun shouldFetch_atExactStaleBoundary() {
        val now = 100_000_000L
        assertTrue(shouldFetch(lastSavedMillis = now - FETCH_STALE_MILLIS, nowMillis = now))
    }
}
