package com.gametracker.companion.ui.common

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import coil.compose.AsyncImage

@Composable
fun CoverImage(url: String?, title: String, modifier: Modifier = Modifier) {
    if (url.isNullOrBlank()) {
        Box(modifier, contentAlignment = Alignment.Center) {
            Text(title, style = MaterialTheme.typography.labelSmall)
        }
    } else {
        AsyncImage(model = url, contentDescription = title,
            modifier = modifier, contentScale = ContentScale.Crop)
    }
}
