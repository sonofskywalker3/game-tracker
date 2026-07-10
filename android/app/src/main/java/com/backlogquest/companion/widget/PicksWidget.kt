package com.backlogquest.companion.widget

import android.content.Context
import android.graphics.Bitmap
import android.graphics.drawable.BitmapDrawable
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.background
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.provideContent
import androidx.glance.layout.Alignment
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.width
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import coil.imageLoader
import coil.request.ImageRequest
import coil.request.SuccessResult
import com.backlogquest.companion.App
import com.backlogquest.companion.MainActivity
import com.backlogquest.companion.R
import com.backlogquest.companion.ui.picks.deviceNowWeekdayMinute

/** Widget card colors = the app's dark palette (ui/theme/Theme.kt): OnSurface
 *  title, indigo primary for the slot label, OnSurfaceVariant secondary text,
 *  on the #181A22 surface card drawable. */
private val TitleColor = ColorProvider(Color(0xFFE6E6EC))   // OnSurface
private val SlotColor = ColorProvider(Color(0xFF8B93FF))    // Indigo (primary)
private val BodyColor = ColorProvider(Color(0xFFB7B9C6))    // OnSurfaceVariant

/** Intent extra key used by MainActivity to open the Picks tab on launch (consumed by Task 8). */
const val EXTRA_OPEN_TAB = "open_tab"

/**
 * Glance action parameter delivered as the [EXTRA_OPEN_TAB] launch-Intent extra. Using a
 * stable parameter key (instead of building a fresh Intent in the composable) keeps the tap
 * action identity stable across recompositions.
 */
private val OpenTabParam = ActionParameters.Key<String>(EXTRA_OPEN_TAB)

class PicksWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val store = (context.applicationContext as App).container.scheduleSnapshotStore
        val snapshot = store.load()
        val card: WidgetCard? = snapshot?.let {
            val (weekday, minute) = deviceNowWeekdayMinute()
            buildWidgetCard(it.slots, weekday, minute)
        }
        val cover: Bitmap? = card?.coverUrl?.let { loadCoverBitmap(context, it) }
        provideContent { WidgetBody(card, cover) }
    }

    /** Best-effort cover load → Bitmap for ImageProvider; null on any failure. */
    private suspend fun loadCoverBitmap(context: Context, url: String): Bitmap? {
        return try {
            val loader = context.imageLoader
            val request = ImageRequest.Builder(context)
                .data(url)
                .allowHardware(false)
                .build()
            val result = loader.execute(request)
            if (result !is SuccessResult) return null
            (result.drawable as? BitmapDrawable)?.bitmap
        } catch (_: Exception) {
            null
        }
    }
}

@Composable
private fun WidgetBody(card: WidgetCard?, cover: Bitmap?) {
    Row(
        modifier = GlanceModifier
            .fillMaxSize()
            .background(ImageProvider(R.drawable.widget_bg))
            .padding(12.dp)
            .clickable(
                actionStartActivity<MainActivity>(actionParametersOf(OpenTabParam to "picks")),
            ),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (cover != null) {
            Image(
                provider = ImageProvider(cover),
                contentDescription = card?.title ?: "Pick",
                modifier = GlanceModifier.width(72.dp).height(96.dp),
            )
            Spacer(GlanceModifier.width(12.dp))
        }
        Column {
            Text(
                text = card?.title ?: "No picks scheduled",
                style = TextStyle(fontSize = 16.sp, color = TitleColor),
            )
            if (card != null && card.slotLabel.isNotEmpty()) {
                Text(
                    text = card.slotLabel,
                    style = TextStyle(fontSize = 13.sp, color = SlotColor),
                )
            }
            Text(
                text = card?.hint ?: "Set windows on the web",
                style = TextStyle(fontSize = 13.sp, color = BodyColor),
            )
            if (card?.goal != null) {
                Text(
                    text = "Goal: ${card.goal}",
                    style = TextStyle(fontSize = 12.sp, color = BodyColor),
                )
            }
        }
    }
}
