@file:OptIn(com.google.firebase.ai.type.PublicPreviewAPI::class)

package com.woadivector.proofofloss

import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import com.google.firebase.Firebase
import com.google.firebase.ai.DownloadStatus
import com.google.firebase.ai.OnDeviceModelStatus
import com.google.firebase.appcheck.appCheck
import com.google.firebase.appcheck.debug.DebugAppCheckProviderFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.time.Instant

class CaptureActivity : ComponentActivity() {

    private lateinit var store: WalkStore

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // App Check: the debug provider prints a token to Logcat on first run; register it in the
        // Firebase console (App Check, Apps, Android app, Manage debug tokens).
        Firebase.appCheck.installAppCheckProviderFactory(DebugAppCheckProviderFactory.getInstance())
        store = WalkStore(filesDir)
        enableEdgeToEdge()
        setContent { PolTheme { WalkScreen(store) } }
    }
}

@Composable
fun PolTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val ctx = LocalContext.current
    val scheme = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        dark -> darkColorScheme()
        else -> lightColorScheme()
    }
    MaterialTheme(colorScheme = scheme, content = content)
}

private val DEVICE_GREEN = Color(0xFF2F6D3A)
private val CLOUD_BLUE = Color(0xFF1F5F8B)
private val WARN = Color(0xFF8A5A00)

@Composable
fun WalkScreen(store: WalkStore) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var walk by remember {
        mutableStateOf(store.load() ?: store.start("", "${Build.MANUFACTURER} ${Build.MODEL}"))
    }
    var mode by rememberSaveable { mutableStateOf(Mode.PREFER_ON_DEVICE) }
    val spotter = remember(mode) { Spotter(mode) }
    var room by rememberSaveable { mutableStateOf(walk.captures.firstOrNull()?.room?.takeIf { it != "Unnamed room" } ?: "") }
    var modelStatus by remember { mutableStateOf("Checking for Gemini Nano on this phone") }
    var canDownload by remember { mutableStateOf(false) }
    var working by remember { mutableStateOf(setOf<String>()) }

    fun update(w: Walk) {
        walk = w
        store.save(w)
    }

    fun replace(sha: String, f: (Capture) -> Capture) {
        update(walk.copy(captures = walk.captures.map { if (it.sha256 == sha) f(it) else it }))
    }

    suspend fun spotOne(sha: String) {
        val c = walk.captures.firstOrNull { it.sha256 == sha } ?: return
        working = working + sha
        try {
            val bmp: Bitmap = withContext(Dispatchers.IO) { loadScaled(store.photo(c), 1024) }
                ?: throw IllegalStateException("could not read the photo")
            val r = spotter.spot(bmp)
            val (notProp, rest) = r.items.partition { isNotProperty(it.name) }
            val (structure, contents) = rest.partition { isStructure(it.name) }
            replace(sha) { it.copy(items = contents, source = r.source, modelMs = r.ms, modelError = null,
                raw = if (r.items.isEmpty()) r.raw else null, setAside = structure.map { s -> s.name },
                notProperty = notProp.map { s -> s.name }) }
        } catch (e: Exception) {
            replace(sha) { it.copy(modelError = e.message ?: e.javaClass.simpleName) }
        } finally {
            working = working - sha
        }
    }

    fun look(sha: String) {
        scope.launch { spotOne(sha) }
    }

    var pulling by remember { mutableStateOf<String?>(null) } // video sha being processed
    var pullNote by remember { mutableStateOf("") }

    fun pull(v: VideoRecord) {
        pulling = v.sha256
        scope.launch {
            try {
                val frames = withContext(Dispatchers.IO) {
                    pullFrames(store.video(v), store.dir) { msg -> pullNote = msg }
                }
                val start = runCatching { Instant.parse(v.startedAt) }.getOrNull()
                val added = mutableListOf<String>()
                for (fr in frames) {
                    val (sha, file) = withContext(Dispatchers.IO) { store.accept(fr.file) }
                    if (walk.captures.none { it.sha256 == sha }) {
                        val c = Capture(
                            sha256 = sha, file = file.name, room = v.room,
                            capturedAt = (start?.plusMillis(fr.ms) ?: Instant.now()).toString(),
                            recordedAt = null, fromVideo = v.sha256, atMs = fr.ms,
                        )
                        update(walk.copy(captures = walk.captures + c))
                        added += sha
                    }
                }
                // Gemini Nano reads one frame at a time: the on-device model takes one image per request.
                added.forEachIndexed { i, sha ->
                    pullNote = "Reading frame ${i + 1} of ${added.size}"
                    spotOne(sha)
                }
                pullNote = "${added.size} frames pulled and read"
            } catch (e: Exception) {
                pullNote = "Could not pull frames: ${e.message}"
            } finally {
                pulling = null
            }
        }
    }

    LaunchedEffect(spotter) {
        val s = runCatching { spotter.status() }.getOrNull()
        canDownload = s == OnDeviceModelStatus.DOWNLOADABLE
        modelStatus = when (s) {
            OnDeviceModelStatus.AVAILABLE -> "Gemini Nano is ready on this phone"
            OnDeviceModelStatus.DOWNLOADABLE -> "Gemini Nano can be downloaded to this phone"
            OnDeviceModelStatus.DOWNLOADING -> "Gemini Nano is downloading"
            OnDeviceModelStatus.UNAVAILABLE -> "Gemini Nano is not available on this phone; cloud Gemini is used"
            null -> "On-device model not offered; cloud Gemini is used"
            else -> "On-device model status: $s"
        }
        if (s == OnDeviceModelStatus.AVAILABLE) spotter.warmUp()
    }

    var cameraOpen by rememberSaveable { mutableStateOf(false) }

    fun acceptPhoto(pending: File) {
        if (!pending.exists() || pending.length() == 0L) { pending.delete(); return }
        scope.launch {
            // Hash first, before anything else reads or touches the file.
            val (sha, file) = withContext(Dispatchers.IO) { store.accept(pending) }
            if (walk.captures.none { it.sha256 == sha }) {
                val c = Capture(
                    sha256 = sha, file = file.name, room = room.trim(),
                    capturedAt = Instant.now().toString(), recordedAt = exifDate(file),
                )
                update(walk.copy(captures = listOf(c) + walk.captures))
                look(sha)
            }
        }
    }

    fun acceptVideo(pending: File, startedAt: String, durationMs: Long) {
        if (!pending.exists() || pending.length() == 0L) { pending.delete(); return }
        scope.launch {
            val (sha, file) = withContext(Dispatchers.IO) { store.acceptVideo(pending) }
            if (walk.videos.none { it.sha256 == sha }) {
                update(walk.copy(videos = listOf(VideoRecord(sha, file.name, room.trim(), startedAt, durationMs)) + walk.videos))
            }
        }
    }

    if (cameraOpen) {
        CameraScreen(
            room = room,
            newPhotoFile = { store.newPhotoFile() },
            newVideoFile = { store.newVideoFile() },
            onPhoto = { acceptPhoto(it) },
            onVideo = { f, startedAt, ms -> acceptVideo(f, startedAt, ms) },
            onClose = { cameraOpen = false },
        )
        return
    }

    fun share() {
        store.save(walk)
        val uris = ArrayList<Uri>()
        uris += FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", store.recordFile())
        walk.captures.forEach { uris += FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", store.photo(it)) }
        walk.videos.forEach { uris += FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", store.video(it)) }
        val send = Intent(Intent.ACTION_SEND_MULTIPLE).apply {
            type = "*/*"
            putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        ctx.startActivity(Intent.createChooser(send, "Share walk"))
    }

    val onDevice = walk.captures.count { it.source == "on_device" }
    val inCloud = walk.captures.count { it.source == "in_cloud" }

    Scaffold(modifier = Modifier.fillMaxSize()) { pad ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(pad).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Spacer(Modifier.height(8.dp))
                Text("Proof of Loss", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                Text("Walk capture. Nothing leaves this phone until you share the walk.",
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(modelStatus, style = MaterialTheme.typography.bodyMedium)
                        if (canDownload) {
                            OutlinedButton(onClick = {
                                canDownload = false
                                scope.launch {
                                    spotter.download()?.collect { d ->
                                        modelStatus = when (d) {
                                            is DownloadStatus.DownloadInProgress ->
                                                "Downloading Gemini Nano: ${d.totalBytesDownloaded / 1_000_000} MB"
                                            is DownloadStatus.DownloadFailed -> "Gemini Nano download failed; cloud Gemini is used"
                                            DownloadStatus.DownloadCompleted -> "Gemini Nano is ready on this phone"
                                            else -> "Starting the Gemini Nano download"
                                        }
                                    }
                                }
                            }) { Text("Download Gemini Nano") }
                        }
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            items(Mode.entries) { m ->
                                if (m == mode) Button(onClick = {}) { Text(m.label) }
                                else OutlinedButton(onClick = { mode = m }) { Text(m.label) }
                            }
                        }
                        Text("$onDevice photos read on device · $inCloud in the cloud",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            item {
                RoomPicker(room, walk.captures.map { it.room }) { room = it }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { cameraOpen = true }, enabled = room.isNotBlank(), modifier = Modifier.weight(1f)) {
                        Text(if (room.isBlank()) "Choose a room first" else "Open camera")
                    }
                    OutlinedButton(onClick = { share() }, enabled = walk.captures.isNotEmpty() || walk.videos.isNotEmpty()) { Text("Share walk") }
                }
            }
            items(walk.videos, key = { it.sha256 }) { v ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text("Walk video · ${v.room}", fontWeight = FontWeight.SemiBold)
                        Text("Captured in app · ${v.startedAt.take(16).replace('T', ' ')} UTC · " +
                            "${v.durationMs / 60000}:${"%02d".format((v.durationMs / 1000) % 60)}",
                            style = MaterialTheme.typography.bodySmall)
                        Text("SHA-256 ${v.sha256.take(16)}…", fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("Kept unchanged for review. Frames are copies; the video is never edited.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        val frameCount = walk.captures.count { it.fromVideo == v.sha256 }
                        if (pulling == v.sha256) Text(pullNote, style = MaterialTheme.typography.bodySmall)
                        else {
                            if (frameCount > 0) Text("$frameCount frames from this video", style = MaterialTheme.typography.bodySmall)
                            OutlinedButton(onClick = { pull(v) }, enabled = pulling == null) {
                                Text(if (frameCount == 0) "Pull frames" else "Pull frames again")
                            }
                        }
                    }
                }
            }
            val inventory = buildInventory(walk.captures)
            if (inventory.isNotEmpty()) item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("Room inventory", fontWeight = FontWeight.SemiBold)
                        Text("${inventory.size} belongings from ${walk.captures.count { it.source != null }} photos and frames. " +
                            "Repeats are rolled up, never dropped; each line keeps every sighting.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        inventory.groupBy { it.room }.forEach { (r, lines) ->
                            Text(r, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 4.dp))
                            lines.forEach { ln ->
                                val times = ln.sightings.mapNotNull { it.atMs }.sorted()
                                val span = if (times.isNotEmpty()) ", ${fmt(times.first())} to ${fmt(times.last())}" else ""
                                Text(buildString {
                                    append("• ").append(ln.name)
                                    append("  (seen ${ln.sightings.size}×$span")
                                    if (ln.clearCount < ln.sightings.size) append("; ${ln.clearCount} clear")
                                    append(")")
                                }, style = MaterialTheme.typography.bodySmall)
                                if (ln.brandReadings.isNotEmpty()) {
                                    // A brand read only once is never treated as the brand: small text blurs in video.
                                    val agreed = ln.brandReadings.entries.size == 1 && ln.brandReadings.values.first() >= 2
                                    Text("   brand read as " + ln.brandReadings.entries.joinToString(", ") {
                                        "${it.key} ×${it.value}" + if (it.value == 1) " (unconfirmed)" else "" } +
                                        if (agreed) "" else "; adjuster to confirm",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = if (agreed) MaterialTheme.colorScheme.onSurfaceVariant else WARN)
                                }
                            }
                        }
                    }
                }
            }
            items(walk.captures.sortedWith(compareBy({ it.fromVideo != null }, { it.atMs ?: 0L })), key = { it.sha256 }) { c ->
                CaptureCard(c, store.photo(c), c.sha256 in working) { look(c.sha256) }
            }
            item {
                TextButton(onClick = {
                    walk = store.start("", "${Build.MANUFACTURER} ${Build.MODEL}")
                }) { Text("Start a new walk (this one is kept on the phone)") }
                Spacer(Modifier.height(24.dp))
            }
        }
    }
}

@Composable
fun SourceTag(source: String?) {
    if (source == null) return
    val (label, color) = if (source == "on_device") "On device" to DEVICE_GREEN else "Cloud" to CLOUD_BLUE
    Surface(color = color, shape = RoundedCornerShape(4.dp)) {
        Text(label, color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 1.dp))
    }
}

@Composable
fun CaptureCard(c: Capture, file: File, busy: Boolean, onRetry: () -> Unit) {
    val thumb by produceState<Bitmap?>(null, file) {
        value = withContext(Dispatchers.IO) { loadScaled(file, 400) }
    }
    Card(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Box(Modifier.size(96.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(6.dp))) {
                thumb?.let {
                    Image(it.asImageBitmap(), contentDescription = c.room, contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize())
                }
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(c.room, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.width(6.dp))
                    SourceTag(c.source)
                }
                Text(if (c.atMs != null) "Frame at ${c.atMs / 60000}:${"%02d".format((c.atMs / 1000) % 60)} of walk video"
                    else "Captured in app · ${c.capturedAt.take(16).replace('T', ' ')} UTC",
                    style = MaterialTheme.typography.bodySmall)
                Text("SHA-256 ${c.sha256.take(16)}…", fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                when {
                    busy -> Text("Looking at the photo…", style = MaterialTheme.typography.bodySmall)
                    c.modelError != null -> {
                        Text("Not read yet: ${c.modelError}", color = WARN, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = onRetry) { Text("Look again") }
                    }
                    c.items.isEmpty() && c.source != null -> {
                        Text("No household items named.", style = MaterialTheme.typography.bodySmall)
                        if (!c.raw.isNullOrBlank()) Text("Model said: ${c.raw}", fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        TextButton(onClick = onRetry) { Text("Look again") }
                    }
                    else -> c.items.forEach { it2 ->
                        Text(
                            buildString {
                                append("• ").append(it2.name)
                                if (it2.brandModel.isNotBlank()) append(" (").append(it2.brandModel).append(")")
                                if (!it2.clear) append("  [unclear frame]")
                            },
                            style = MaterialTheme.typography.bodySmall,
                            color = if (it2.clear) MaterialTheme.colorScheme.onSurface else WARN,
                        )
                    }
                }
                if (c.notProperty.isNotEmpty()) {
                    Text("Set aside, not property: ${c.notProperty.joinToString(", ")}",
                        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (c.setAside.isNotEmpty()) {
                    Text("Set aside as part of the building: ${c.setAside.joinToString(", ")}",
                        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (c.modelMs != null) {
                    Text("${"%.1f".format(c.modelMs / 1000.0)} s", style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

/** Standard room names, so one room never appears under two spellings in a walk. */
val STANDARD_ROOMS = listOf(
    "Living room", "Family room", "Kitchen", "Dining room", "Primary bedroom", "Bedroom 2",
    "Bedroom 3", "Bedroom 4", "Primary bathroom", "Bathroom 2", "Half bath", "Office", "Laundry room",
    "Garage", "Basement", "Attic", "Hallway", "Closet", "Mudroom", "Patio or porch", "Shed",
)

@Composable
fun RoomPicker(selected: String, used: List<String>, onPick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    var typing by rememberSaveable { mutableStateOf(false) }
    var custom by rememberSaveable { mutableStateOf("") }
    // Rooms already used on this walk come first; matching ignores letter case.
    val extra = used.filter { u -> u.isNotBlank() && u != "Unnamed room" &&
        STANDARD_ROOMS.none { it.equals(u, ignoreCase = true) } }.distinctBy { it.lowercase() }
    Column {
        Text("Room", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Box {
            OutlinedButton(onClick = { open = true }, modifier = Modifier.fillMaxWidth()) {
                Text(selected.ifBlank { "Choose a room" }, modifier = Modifier.weight(1f))
                Text("▾")
            }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                extra.forEach { r ->
                    DropdownMenuItem(text = { Text(r) }, onClick = { onPick(r); typing = false; open = false })
                }
                if (extra.isNotEmpty()) HorizontalDivider()
                STANDARD_ROOMS.forEach { r ->
                    DropdownMenuItem(text = { Text(r) }, onClick = { onPick(r); typing = false; open = false })
                }
                HorizontalDivider()
                DropdownMenuItem(text = { Text("Other…") }, onClick = { typing = true; open = false })
            }
        }
        if (typing) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
                OutlinedTextField(
                    value = custom, onValueChange = { custom = it }, label = { Text("Other room") },
                    singleLine = true, modifier = Modifier.weight(1f),
                )
                TextButton(onClick = {
                    val name = custom.trim()
                    if (name.isNotEmpty()) {
                        // Reuse an existing spelling instead of making a second room.
                        val match = (STANDARD_ROOMS + used).firstOrNull { it.equals(name, ignoreCase = true) }
                        onPick(match ?: name.replaceFirstChar { it.uppercase() })
                        typing = false
                        custom = ""
                    }
                }) { Text("Use") }
            }
        }
    }
}

private fun fmt(ms: Long) = "${ms / 60000}:${"%02d".format((ms / 1000) % 60)}"
