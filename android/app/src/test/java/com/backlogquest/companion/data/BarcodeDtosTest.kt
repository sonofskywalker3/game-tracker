package com.backlogquest.companion.data

import org.junit.Assert.*
import org.junit.Test

class BarcodeDtosTest {
    @Test fun parses_igdb_result_with_year_and_igdb_url() {
        val json = appJson()
        val results = json.decodeFromString<List<IgdbResult>>(
            """[{"name":"Hades","slug":"hades","cover_url":"https://x/c.jpg",
                 "igdb_url":"https://www.igdb.com/games/hades","year":2020,
                 "platforms":["PC","Switch"]}]"""
        )
        val r = results[0]
        assertEquals("Hades", r.name)
        assertEquals("https://www.igdb.com/games/hades", r.igdbUrl)
        assertEquals(2020, r.year)
        assertEquals(listOf("PC", "Switch"), r.platforms)
    }

    @Test fun parses_igdb_result_with_null_year_and_igdb_url() {
        val json = appJson()
        val results = json.decodeFromString<List<IgdbResult>>(
            """[{"name":"Unknown Game","platforms":[]}]"""
        )
        val r = results[0]
        assertEquals("Unknown Game", r.name)
        assertNull(r.igdbUrl)
        assertNull(r.year)
    }

    @Test fun parses_enhanced_resolve_with_ownership_and_constituents() {
        val json = appJson()
        val body = json.decodeFromString<BarcodeResolveResponse>(
            """{"upc":"045496590475","source":"upc_api","scanned_platform":"Switch",
                "candidates":[
                  {"igdb_id":26764,"title":"Mario Kart 8 Deluxe","platform":"Switch",
                   "cover_url":"https://x/c.jpg","game_type":10,"owned_game_id":341,
                   "owned_platforms":[{"short_name":"Switch","format":"digital","has_digital_market":1}]},
                  {"igdb_id":203219,"title":"MK8D + Super Mario Party Double Pack","platform":"Switch",
                   "cover_url":"https://x/d.jpg","game_type":3,"owned_game_id":null,"owned_platforms":[],
                   "constituents":[
                     {"title":"Super Mario Party","owned_game_id":null,"owned_platforms":[]},
                     {"title":"Mario Kart 8 Deluxe","owned_game_id":341,
                      "owned_platforms":[{"short_name":"Switch","format":"digital","has_digital_market":1}]}]}]}"""
        )
        assertEquals("Switch", body.scannedPlatform)
        val top = body.candidates[0]
        assertEquals(341, top.ownedGameId)
        assertEquals("Switch", top.ownedPlatforms[0].shortName)
        assertEquals("digital", top.ownedPlatforms[0].format)
        assertEquals(1, top.ownedPlatforms[0].hasDigitalMarket)
        val bundle = body.candidates[1]
        assertEquals(3, bundle.gameType)
        assertEquals(2, bundle.constituents.size)
        assertEquals(341, bundle.constituents[1].ownedGameId)
        assertEquals("Switch", bundle.constituents[1].ownedPlatforms[0].shortName)
    }
}
