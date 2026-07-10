package com.backlogquest.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import com.backlogquest.companion.ui.AppNav
import com.backlogquest.companion.ui.theme.BacklogQuestTheme
import com.backlogquest.companion.widget.EXTRA_OPEN_SLOT_ID
import com.backlogquest.companion.widget.EXTRA_OPEN_TAB

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val openTab = intent?.getStringExtra(EXTRA_OPEN_TAB)
        val openSlotId = intent?.getIntExtra(EXTRA_OPEN_SLOT_ID, -1)?.takeIf { it >= 0 }
        setContent {
            BacklogQuestTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    AppNav(initialTab = openTab, initialSlotId = openSlotId)
                }
            }
        }
    }
}
