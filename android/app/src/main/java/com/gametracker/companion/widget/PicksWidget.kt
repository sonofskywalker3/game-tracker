package com.gametracker.companion.widget

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.drawable.BitmapDrawable
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalContext
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.action.actionStartActivity
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
import coil.ImageLoader
import coil.request.ImageRequest
import coil.request.SuccessResult
import com.gametracker.companion.App
import com.gametracker.companion.MainActivity
import com.gametracker.companion.ui.picks.deviceNowWeekdayMinute

/** Intent extra key used by MainActivity to open the Picks tab on launch (consumed by Task 8). */
const val EXTRA_OPEN_TAB = "open_tab"

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
            val loader = ImageLoader(context)
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
    val context = LocalContext.current
    val launchIntent = Intent(context, MainActivity::class.java).apply {
        putExtra(EXTRA_OPEN_TAB, "picks")
    }
    Row(
        modifier = GlanceModifier
            .fillMaxSize()
            .padding(12.dp)
            .clickable(actionStartActivity(launchIntent)),
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
                style = TextStyle(fontSize = 16.sp),
            )
            if (card != null && card.slotLabel.isNotEmpty()) {
                Text(
                    text = card.slotLabel,
                    style = TextStyle(fontSize = 13.sp),
                )
            }
            Text(
                text = card?.hint ?: "Set windows on the web",
                style = TextStyle(fontSize = 13.sp),
            )
            if (card?.goal != null) {
                Text(
                    text = "Goal: ${card.goal}",
                    style = TextStyle(fontSize = 12.sp),
                )
            }
        }
    }
}
