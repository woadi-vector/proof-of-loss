package com.woadivector.proofofloss

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import kotlinx.coroutines.delay
import java.io.File
import java.time.Instant

/**
 * The in-app camera. Always the rear lens. Proof of Loss writes each file itself, so the hash
 * that follows is of bytes no other app has handled.
 */
@Composable
fun CameraScreen(
    room: String,
    newPhotoFile: () -> File,
    newVideoFile: () -> File,
    onPhoto: (File) -> Unit,
    onVideo: (file: File, startedAt: String, durationMs: Long) -> Unit,
    onClose: () -> Unit,
) {
    val ctx = LocalContext.current
    val owner = ctx as ComponentActivity
    val main = ContextCompat.getMainExecutor(ctx)

    fun granted(p: String) = ContextCompat.checkSelfPermission(ctx, p) == PackageManager.PERMISSION_GRANTED
    var camOk by remember { mutableStateOf(granted(Manifest.permission.CAMERA)) }
    var micOk by remember { mutableStateOf(granted(Manifest.permission.RECORD_AUDIO)) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { r ->
        camOk = r[Manifest.permission.CAMERA] ?: camOk
        micOk = r[Manifest.permission.RECORD_AUDIO] ?: micOk
    }
    LaunchedEffect(Unit) {
        if (!camOk || !micOk) ask.launch(arrayOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO))
    }

    val imageCapture = remember { ImageCapture.Builder().setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY).build() }
    val videoCapture = remember {
        VideoCapture.withOutput(Recorder.Builder().setQualitySelector(QualitySelector.from(Quality.FHD)).build())
    }
    val previewView = remember { PreviewView(ctx) }
    var recording by remember { mutableStateOf<Recording?>(null) }
    var recStart by remember { mutableLongStateOf(0L) }
    var elapsed by remember { mutableLongStateOf(0L) }
    var busy by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf("") }

    DisposableEffect(camOk) {
        var provider: ProcessCameraProvider? = null
        if (camOk) {
            val future = ProcessCameraProvider.getInstance(ctx)
            future.addListener({
                provider = future.get()
                val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
                provider?.unbindAll()
                provider?.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture, videoCapture)
            }, main)
        }
        onDispose {
            recording?.stop()
            provider?.unbindAll()
        }
    }

    LaunchedEffect(recording) {
        while (recording != null) {
            elapsed = System.currentTimeMillis() - recStart
            delay(250)
        }
    }

    fun shoot() {
        val f = newPhotoFile()
        busy = true
        imageCapture.takePicture(ImageCapture.OutputFileOptions.Builder(f).build(), main,
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    busy = false
                    onPhoto(f)
                }
                override fun onError(exception: ImageCaptureException) {
                    busy = false
                    note = "Photo failed: ${exception.message}"
                    f.delete()
                }
            })
    }

    fun toggleVideo() {
        val current = recording
        if (current != null) {
            current.stop() // the Finalize event below hands the file back
            recording = null
            return
        }
        val f = newVideoFile()
        val startedAt = Instant.now().toString()
        val prepared = videoCapture.output.prepareRecording(ctx, FileOutputOptions.Builder(f).build())
        // Narration is part of the record: the adjuster names rooms and items as they walk.
        val withAudio = if (micOk && granted(Manifest.permission.RECORD_AUDIO)) {
            try { prepared.withAudioEnabled() } catch (e: SecurityException) { prepared }
        } else prepared
        recStart = System.currentTimeMillis()
        recording = withAudio.start(main) { ev ->
            if (ev is VideoRecordEvent.Finalize) {
                if (ev.hasError() && (!f.exists() || f.length() == 0L)) {
                    note = "Video failed (error ${ev.error})"
                    f.delete()
                } else {
                    onVideo(f, startedAt, ev.recordingStats.recordedDurationNanos / 1_000_000)
                }
            }
        }
    }

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        if (camOk) AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
        else Text("Camera permission is needed to capture the walk.", color = Color.White,
            modifier = Modifier.align(Alignment.Center).padding(24.dp))

        Column(Modifier.fillMaxSize().safeDrawingPadding().padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                Surface(color = Color(0x99000000), shape = RoundedCornerShape(6.dp)) {
                    Text(room, color = Color.White, fontSize = 16.sp,
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp))
                }
                Box(Modifier.weight(1f))
                if (recording != null) {
                    Surface(color = Color(0xFFB3261E), shape = RoundedCornerShape(6.dp)) {
                        Text("REC ${elapsed / 60000}:${"%02d".format((elapsed / 1000) % 60)}", color = Color.White,
                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp))
                    }
                }
            }
            Box(Modifier.weight(1f))
            if (note.isNotEmpty()) Text(note, color = Color.White, modifier = Modifier.padding(bottom = 8.dp))
            if (recording != null) Text("Say each room as you enter it, and name items as you pass them.",
                color = Color.White, modifier = Modifier.padding(bottom = 8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { recording?.stop(); onClose() },
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White)) { Text("Done") }
                Button(onClick = { shoot() }, enabled = camOk && !busy && recording == null,
                    modifier = Modifier.weight(1f)) { Text("Photo") }
                Button(onClick = { toggleVideo() }, enabled = camOk,
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFB3261E)),
                    modifier = Modifier.weight(1f)) {
                    Text(if (recording == null) "Walk video" else "Stop")
                }
            }
        }
    }
}
