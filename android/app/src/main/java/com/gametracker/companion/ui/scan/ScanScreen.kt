package com.gametracker.companion.ui.scan

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.rememberAppFactory
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.delay

private val PRODUCT_FORMATS = setOf(
    Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E, Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8,
)
private const val REARM_MS = 5000L

// Offered when a scan can't determine the platform (extensible).
private val COMMON_PLATFORMS = listOf("Switch", "PS5", "PS4", "Xbox", "PC", "3DS", "WiiU", "Wii")

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ScanScreen(onOpenGame: (Int) -> Unit, onManualSearch: (String?, String, Boolean) -> Unit) {
    val vm: ScanViewModel = viewModel(factory = rememberAppFactory())
    val context = LocalContext.current
    var granted by remember { mutableStateOf(false) }
    var fired by remember { mutableStateOf(false) }
    val permLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(Unit) { permLauncher.launch(Manifest.permission.CAMERA) }

    if (!granted) {
        Box(Modifier.fillMaxSize(), Alignment.Center) {
            Text("Camera permission is required to scan a barcode.")
        }
        return
    }

    val state = vm.state.collectAsState().value
    val infoMode = vm.infoMode.collectAsState().value
    var barcodePresent by remember { mutableStateOf(false) }
    val reArmGate = remember { PresenceReArmGate() }
    fun rescan() { reArmGate.reset(); fired = false; vm.reset() }

    // Normal mode: timed re-arm after an add (independent of frame presence).
    LaunchedEffect(state, infoMode) {
        if (!infoMode && state is ScanState.Added) { delay(REARM_MS); rescan() }
    }
    // Info mode: re-arm once the item has left the frame (presence gate).
    LaunchedEffect(state, barcodePresent, infoMode) {
        val terminal = state is ScanState.Linked || state is ScanState.Info ||
            state is ScanState.NoMatch || state is ScanState.Added
        if (infoMode && terminal && reArmGate.onFrame(barcodePresent)) { reArmGate.reset(); rescan() }
    }

    Box(Modifier.fillMaxSize()) {
        AndroidView(modifier = Modifier.fillMaxSize(), factory = { ctx ->
            val previewView = PreviewView(ctx)
            val scanner = BarcodeScanning.getClient()
            val providerFuture = ProcessCameraProvider.getInstance(ctx)
            providerFuture.addListener({
                val provider = providerFuture.get()
                val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
                val analysis = ImageAnalysis.Builder().build()
                analysis.setAnalyzer(ContextCompat.getMainExecutor(ctx)) { proxy ->
                    val media = proxy.image
                    if (media != null) {
                        val img = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
                        scanner.process(img)
                            .addOnSuccessListener { codes ->
                                val hit = codes.firstOrNull { it.format in PRODUCT_FORMATS }?.rawValue
                                barcodePresent = hit != null
                                if (hit != null && !fired) { fired = true; vm.onBarcode(hit) }
                            }
                            .addOnFailureListener { barcodePresent = false }
                            .addOnCompleteListener { proxy.close() }
                    } else proxy.close()
                }
                provider.unbindAll()
                provider.bindToLifecycle(ctx as LifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            }, ContextCompat.getMainExecutor(ctx))
            previewView
        })

        Row(Modifier.fillMaxWidth().padding(12.dp),
            horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
            Text("Info mode", style = MaterialTheme.typography.labelLarge)
            Spacer(Modifier.width(8.dp))
            Switch(checked = infoMode, onCheckedChange = { vm.setInfoMode(it) })
        }

        when (val s = state) {
            ScanState.Scanning -> {}
            is ScanState.Resolving -> ResultCard(onDismiss = null) {
                Row(verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    CircularProgressIndicator(Modifier.width(20.dp)); Text("Looking it up…")
                }
            }
            is ScanState.Info -> ResultCard(onDismiss = ::rescan) {
                ScanInfo(s, infoMode = infoMode, onOpenGame = onOpenGame,
                    onAdd = { vm.addToLibrary(s.candidate, s.scannedPlatform, s.upc) },
                    onAddCopy = { p -> vm.addPlatformCopy(s.candidate, p, s.upc) })
            }
            is ScanState.NoMatch -> ResultCard(onDismiss = ::rescan) {
                Text("Couldn't identify that barcode.")
                Button(onClick = { onManualSearch(s.productTitle, s.upc, infoMode) }) { Text("Search manually") }
            }
            is ScanState.Added -> ResultCard(onDismiss = ::rescan) {
                Text("Added ✓")
                s.gameId?.let { Button(onClick = { onOpenGame(it) }) { Text("View") } }
            }
            is ScanState.Error -> ResultCard(onDismiss = ::rescan) { Text(s.message) }
            is ScanState.Picker -> ResultCard(onDismiss = ::rescan) {
                Text("Which one?", style = MaterialTheme.typography.titleSmall)
                s.candidates.forEach { c ->
                    Row(Modifier.fillMaxWidth().clickable {
                        vm.pick(c, s.upc, s.scannedPlatform)
                    }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        CoverImage(c.coverUrl, c.title ?: "", Modifier.width(40.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(c.title ?: "Unknown", Modifier.weight(1f))
                    }
                }
            }
            is ScanState.NeedsPlatform -> ResultCard(onDismiss = ::rescan) {
                Text("${s.candidate.title ?: "This game"} — which platform?",
                    style = MaterialTheme.typography.titleSmall)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    COMMON_PLATFORMS.forEach { p ->
                        AssistChip(onClick = { vm.choosePlatform(s.candidate, p, s.upc) },
                            label = { Text(p) })
                    }
                }
            }
            is ScanState.Linked -> ResultCard(onDismiss = ::rescan) {
                Text("Saved ✓  ${s.title ?: ""} (${s.platform})")
            }
        }
    }
}

@Composable
private fun ScanInfo(s: ScanState.Info, infoMode: Boolean, onOpenGame: (Int) -> Unit,
                     onAdd: () -> Unit, onAddCopy: (String) -> Unit) {
    val c = s.candidate
    Row(verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        CoverImage(c.coverUrl, c.title ?: "", Modifier.width(48.dp))
        Text(c.title ?: "Unknown", Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
    }
    when (ownershipOf(c, s.scannedPlatform)) {
        Ownership.NOT_OWNED -> {
            if (!infoMode) {
                Button(onClick = onAdd) {
                    Text("Add to library" + (s.scannedPlatform?.let { " ($it)" } ?: ""))
                }
            }
        }
        Ownership.SAME_PLATFORM, Ownership.OTHER_PLATFORM -> {
            Text("You already own this on ${ownedLabels(c.ownedPlatforms)}")
            if (!infoMode) {
                s.scannedPlatform?.let { p ->
                    Button(onClick = { onAddCopy(p) }) { Text("Add the $p copy") }
                }
                c.ownedGameId?.let { TextButton(onClick = { onOpenGame(it) }) { Text("View") } }
            }
        }
    }
    // Multi-pack: report which constituents you already own.
    if (c.constituents.isNotEmpty()) {
        HorizontalDivider()
        Text("This collection includes:", style = MaterialTheme.typography.labelMedium)
        c.constituents.forEach { k ->
            val owned = k.ownedPlatforms.isNotEmpty()
            Text(
                if (owned) "✓ ${k.title} — ${ownedLabels(k.ownedPlatforms)}"
                else "• ${k.title}",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun ResultCard(onDismiss: (() -> Unit)?, content: @Composable ColumnScope.() -> Unit) {
    Box(Modifier.fillMaxSize()) {
        // Tap-off the card dismisses + re-arms (only when dismiss is allowed).
        if (onDismiss != null) {
            Box(Modifier.fillMaxSize().clickable(onClick = onDismiss))
        }
        Box(Modifier.fillMaxSize(), Alignment.BottomCenter) {
            Card(Modifier.fillMaxWidth().padding(16.dp)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    content()
                }
            }
        }
    }
}
