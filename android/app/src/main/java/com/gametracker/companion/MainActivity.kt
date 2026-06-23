package com.gametracker.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import com.gametracker.companion.ui.AppNav
import com.gametracker.companion.ui.theme.GameTrackerTheme
import com.gametracker.companion.widget.EXTRA_OPEN_TAB

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val openTab = intent?.getStringExtra(EXTRA_OPEN_TAB)
        setContent {
            GameTrackerTheme {
                Surface(color = MaterialTheme.colorScheme.background) { AppNav(initialTab = openTab) }
            }
        }
    }
}
