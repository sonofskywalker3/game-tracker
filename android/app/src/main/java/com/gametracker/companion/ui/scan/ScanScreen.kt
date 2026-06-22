package com.gametracker.companion.ui.scan

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
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

private val PRODUCT_FORMATS = setOf(
    Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E, Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8,
)

@Composable
fun ScanScreen(onOpenGame: (Int) -> Unit, onManualSearch: (String?, String) -> Unit) {
    val vm: ScanViewModel = viewModel(factory = rememberAppFactory())
    val context = LocalContext.current
    var granted by remember { mutableStateOf(false) }
    var fired by remember { mutableStateOf(false) }
    val permLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(Unit) { permLauncher.launch(Manifest.permission.CAMERA) }

    if (!granted) { Text("Camera permission is required to scan a barcode."); return }

    val state = vm.state.collectAsState().value

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
                                codes.firstOrNull { it.format in PRODUCT_FORMATS }?.rawValue
                                    ?.let { if (!fired) { fired = true; vm.onBarcode(it) } }
                            }
                            .addOnCompleteListener { proxy.close() }
                    } else proxy.close()
                }
                provider.unbindAll()
                provider.bindToLifecycle(ctx as LifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            }, ContextCompat.getMainExecutor(ctx))
            previewView
        })

        // Result overlay
        when (val s = state) {
            is ScanState.Resolving -> ResultCard { CircularProgressIndicator(); Text("Looking it up…") }
            is ScanState.Owned -> ResultCard {
                Text("You own this — ${s.platform ?: "library"}")
                Button(onClick = { onOpenGame(s.gameId) }) { Text("View") }
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Scan again") }
            }
            is ScanState.Candidates -> ResultCard {
                s.candidates.forEach { c: BarcodeCandidate ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CoverImage(c.coverUrl, c.title ?: "", Modifier.width(40.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(c.title ?: "Unknown", Modifier.weight(1f))
                        Button(onClick = { vm.addCandidate(c, s.upc) }) { Text("Add") }
                    }
                }
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Scan again") }
            }
            is ScanState.NoMatch -> ResultCard {
                Text("Couldn't identify that barcode.")
                Button(onClick = { onManualSearch(s.productTitle, s.upc) }) { Text("Search manually") }
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Scan again") }
            }
            is ScanState.Added -> ResultCard {
                Text("Added ✓")
                s.gameId?.let { Button(onClick = { onOpenGame(it) }) { Text("View") } }
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Scan another") }
            }
            is ScanState.Error -> ResultCard {
                Text(s.message)
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Try again") }
            }
            ScanState.Scanning -> {}
        }
    }
}

@Composable
private fun ResultCard(content: @Composable ColumnScope.() -> Unit) {
    Box(Modifier.fillMaxSize(), Alignment.BottomCenter) {
        Card(Modifier.fillMaxWidth().padding(16.dp)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
                content = content)
        }
    }
}
