package com.backlogquest.companion.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Branded BacklogQuest dark palette — an indigo/violet primary with a warm amber
// accent, on near-black surfaces. Tuned for AMOLED-ish contrast and cover art.
private val Indigo        = Color(0xFF8B93FF)   // primary
private val IndigoDark    = Color(0xFF2A2E5A)   // primary container
private val Teal          = Color(0xFF4FD1C5)   // secondary
private val TealDark      = Color(0xFF14373A)   // secondary container
private val Amber         = Color(0xFFFFB74D)   // tertiary (accent / chips)
private val AmberDark     = Color(0xFF463011)
private val Bg            = Color(0xFF0E0F14)   // background
private val SurfaceCard   = Color(0xFF181A22)   // surface
private val SurfaceVar    = Color(0xFF242736)   // surfaceVariant (cover backdrop)
private val OnSurface     = Color(0xFFE6E6EC)
private val OnSurfaceVar  = Color(0xFFB7B9C6)

private val BacklogQuestDark = darkColorScheme(
    primary            = Indigo,
    onPrimary          = Color(0xFF14163A),
    primaryContainer   = IndigoDark,
    onPrimaryContainer = Color(0xFFDDE0FF),
    secondary          = Teal,
    onSecondary        = Color(0xFF003733),
    secondaryContainer = TealDark,
    onSecondaryContainer = Color(0xFFB8FFF6),
    tertiary           = Amber,
    onTertiary         = Color(0xFF3A2600),
    tertiaryContainer  = AmberDark,
    background         = Bg,
    onBackground       = OnSurface,
    surface            = SurfaceCard,
    onSurface          = OnSurface,
    surfaceVariant     = SurfaceVar,
    onSurfaceVariant   = OnSurfaceVar,
    outlineVariant     = Color(0xFF3A3D4D),
)

@Composable
fun BacklogQuestTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = BacklogQuestDark, content = content)
}
