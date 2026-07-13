package com.backlogquest.companion.widget

import android.content.Context
import android.graphics.Bitmap
import android.graphics.drawable.BitmapDrawable
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.datastore.preferences.core.Preferences
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalSize
import androidx.glance.background
import androidx.glance.action.Action
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.provideContent
import androidx.glance.currentState
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
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
import com.backlogquest.companion.data.ScheduleSnapshot
import com.backlogquest.companion.ui.picks.deviceNowWeekdayMinute

/** Widget card colors = the app's dark palette (ui/theme/Theme.kt): OnSurface
 *  title, indigo primary for the slot label, OnSurfaceVariant secondary text,
 *  on the #181A22 surface card drawable. */
private val TitleColor = ColorProvider(Color(0xFFE6E6EC))   // OnSurface
private val SlotColor = ColorProvider(Color(0xFF8B93FF))    // Indigo (primary)
private val BodyColor = ColorProvider(Color(0xFFB7B9C6))    // OnSurfaceVariant

/** Intent extra key used by MainActivity to open the Picks tab on launch (consumed by Task 8). */
const val EXTRA_OPEN_TAB = "open_tab"

/** Intent extra: slot id whose panel the Picks pager should open on. */
const val EXTRA_OPEN_SLOT_ID = "open_slot_id"

private val OpenSlotParam = ActionParameters.Key<Int>(EXTRA_OPEN_SLOT_ID)

/**
 * Glance action parameter delivered as the [EXTRA_OPEN_TAB] launch-Intent extra. Using a
 * stable parameter key (instead of building a fresh Intent in the composable) keeps the tap
 * action identity stable across recompositions.
 */
private val OpenTabParam = ActionParameters.Key<String>(EXTRA_OPEN_TAB)

class PicksWidget : GlanceAppWidget() {
    /** Recompose per exact size so the card scales to fill tall/wide placements. */
    override val sizeMode: SizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val store = (context.applicationContext as App).container.scheduleSnapshotStore
        val snapshot = store.load()
        // Preload every cycle slot's cover up front: the composable below re-runs on each
        // prev/next press (via currentState) and cannot suspend to fetch a bitmap then.
        val covers: Map<String, Bitmap> = snapshot?.let { snap ->
            val (weekday, minute) = deviceNowWeekdayMinute()
            buildCycleList(snap.slots.slots, weekday, minute)
                .mapNotNull { it.currentGame?.coverUrl }
                .distinct()
                .mapNotNull { url -> loadCoverBitmap(context, url)?.let { url to it } }
                .toMap()
        } ?: emptyMap()
        provideContent { WidgetContent(snapshot, covers) }
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

/**
 * Reads the manual selection via [currentState] so a CycleAction press recomposes with the
 * NEW index. (Computing the card in provideGlance froze the widget: while the Glance session
 * is warm, update() only recomposes — code before provideContent never re-runs.)
 */
@Composable
private fun WidgetContent(snapshot: ScheduleSnapshot?, covers: Map<String, Bitmap>) {
    val prefs = currentState<Preferences>()
    val (weekday, minute) = deviceNowWeekdayMinute()
    var cycleSize = 0
    val card: WidgetCard? = snapshot?.let {
        cycleSize = buildCycleList(it.slots.slots, weekday, minute).size
        val index = effectiveIndex(
            prefs[SelIndexKey] ?: 0, prefs[SelAtKey] ?: 0L,
            System.currentTimeMillis(), cycleSize,
        )
        buildWidgetCard(it.slots, weekday, minute, index)
    }
    WidgetBody(card, card?.coverUrl?.let(covers::get), cycleSize)
}

@Composable
private fun WidgetBody(card: WidgetCard?, cover: Bitmap?, cycleSize: Int) {
    val openPicks: Action = if (card?.deepLinkSlotId != null) {
        actionStartActivity<MainActivity>(
            actionParametersOf(OpenTabParam to "picks", OpenSlotParam to card.deepLinkSlotId),
        )
    } else {
        actionStartActivity<MainActivity>(actionParametersOf(OpenTabParam to "picks"))
    }
    // Scale the 3:4 cover to the height left above the button row (32dp buttons + 24dp
    // vertical padding), so the card fits a 2-row placement (SizeMode.Exact).
    val coverHeight = (LocalSize.current.height - 56.dp).coerceIn(48.dp, 168.dp)
    val coverWidth = coverHeight * 3 / 4
    val tall = coverHeight >= 128.dp
    Column(
        modifier = GlanceModifier
            .fillMaxSize()
            .background(ImageProvider(R.drawable.widget_bg))
            .padding(12.dp),
    ) {
        Row(
            modifier = GlanceModifier.fillMaxWidth().defaultWeight().clickable(openPicks),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (cover != null) {
                Image(
                    provider = ImageProvider(cover),
                    contentDescription = card?.title ?: "Pick",
                    modifier = GlanceModifier.width(coverWidth).height(coverHeight),
                )
                Spacer(GlanceModifier.width(12.dp))
            }
            Column {
                Text(
                    text = card?.title ?: "No picks scheduled",
                    style = TextStyle(fontSize = if (tall) 18.sp else 16.sp, color = TitleColor),
                )
                if (card != null && card.slotLabel.isNotEmpty()) {
                    Text(
                        text = card.slotLabel,
                        style = TextStyle(fontSize = if (tall) 14.sp else 13.sp, color = SlotColor),
                    )
                }
                Text(
                    text = card?.hint ?: "Set windows on the web",
                    style = TextStyle(fontSize = if (tall) 14.sp else 13.sp, color = BodyColor),
                )
                if (card?.goal != null) {
                    Text(
                        text = "Goal: ${card.goal}",
                        style = TextStyle(fontSize = if (tall) 13.sp else 12.sp, color = BodyColor),
                    )
                }
            }
        }
        if (cycleSize > 1) {
            Row(
                modifier = GlanceModifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Image(
                    provider = ImageProvider(R.drawable.ic_widget_prev),
                    contentDescription = "Previous pick",
                    modifier = GlanceModifier.size(32.dp).clickable(
                        actionRunCallback<CycleAction>(actionParametersOf(DirectionParam to -1)),
                    ),
                )
                Box(modifier = GlanceModifier.defaultWeight()) {}
                Image(
                    provider = ImageProvider(R.drawable.ic_widget_next),
                    contentDescription = "Next pick",
                    modifier = GlanceModifier.size(32.dp).clickable(
                        actionRunCallback<CycleAction>(actionParametersOf(DirectionParam to +1)),
                    ),
                )
            }
        }
    }
}
