package com.gametracker.companion.ui.common

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage

@Composable
fun CoverImage(url: String?, title: String, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(8.dp)
    val base = modifier
        .aspectRatio(3f / 4f)
        .clip(shape)
        .background(MaterialTheme.colorScheme.surfaceVariant)
    if (url.isNullOrBlank()) {
        Box(base, contentAlignment = Alignment.Center) {
            Text(
                title,
                style = MaterialTheme.typography.labelSmall,
                textAlign = TextAlign.Center,
            )
        }
    } else {
        AsyncImage(
            model = url,
            contentDescription = title,
            modifier = base.fillMaxSize(),
            contentScale = ContentScale.Fit,
        )
    }
}
