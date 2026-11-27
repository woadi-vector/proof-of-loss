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
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.runtime.collectAsState
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

    companion object {
        /** True while this app is the one on screen: the only time Gemini Nano will run. */
        val onScreen = kotlinx.coroutines.flow.MutableStateFlow(false)
    }

    override fun onResume() { super.onResume(); onScreen.value = true }
    override fun onPause() { onScreen.value = false; super.onPause() }

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
    // Used only when the adjuster asks for it on a frame the on-device model turned down.
    val cloudSpotter = remember { Spotter(Mode.ONLY_IN_CLOUD) }
    val listener = remember { Listener() }
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

    suspend fun spotOne(sha: String, cloud: Boolean = false) {
        val c = walk.captures.firstOrNull { it.sha256 == sha } ?: return
        working = working + sha
        try {
            val bmp: Bitmap = withContext(Dispatchers.IO) { loadScaled(store.photo(c), 1024) }
                ?: throw IllegalStateException("could not read the photo")
            var r: SpotResult? = null
            for (attempt in 0 until 3) {
                try { r = (if (cloud) cloudSpotter else spotter).spot(bmp); break }
                catch (e: Exception) {
                    // AICore limits how fast one app may ask; wait and try again.
                    if (attempt < 2 && "busy" in (e.message ?: "").lowercase()) kotlinx.coroutines.delay(2000L shl attempt)
                    else throw e
                }
            }
            r!!
            val (notProp, rest) = r.items.partition { isNotProperty(it.name) }
            val (structure, contents) = rest.partition { isStructure(it.name) }
            replace(sha) { it.copy(items = contents, source = r.source, modelMs = r.ms, modelError = null,
                raw = if (r.items.isEmpty()) r.raw else null, setAside = structure.map { s -> s.name },
                notProperty = notProp.map { s -> s.name }) }
        } catch (e: Exception) {
            val msg = e.message ?: e.javaClass.simpleName
            replace(sha) { it.copy(modelError = when {
                !CaptureActivity.onScreen.value || "background" in msg.lowercase() ->
                    "$PAUSED the app was not on screen, and Gemini Nano only runs while it is. Resumes when you return."
                "policy" in msg.lowercase() -> "the on-device model declined this frame (its safety filter). $msg"
                else -> msg
            }) }
        } finally {
            working = working - sha
        }
    }

    fun look(sha: String, cloud: Boolean = false) {
        scope.launch { spotOne(sha, cloud) }
    }

    var pulling by remember { mutableStateOf<String?>(null) } // video sha being processed
    var pullNote by remember { mutableStateOf("") }

    suspend fun doPull(v: VideoRecord) {
        pulling = v.sha256
        run {
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

    suspend fun doListen(v: VideoRecord) {
        pulling = v.sha256
        run {
            val audio = File(store.dir, "listen_${v.sha256.take(12)}.m4a")
            try {
                pullNote = "Copying the narration out of the video"
                val ok = withContext(Dispatchers.IO) { extractAudio(store.video(v), audio) }
                if (!ok) throw IllegalStateException("this video has no audio track")
                pullNote = "Listening in the cloud (needs signal)"
                val heard = listener.listen(audio, v.durationMs)
                val mentions = heard.mentions
                update(walk.copy(videos = walk.videos.map {
                    if (it.sha256 == v.sha256) it.copy(mentions = mentions, transcript = heard.transcript,
                        listenSource = "in_cloud", listenError = null) else it }))
                // Rooms follow what the adjuster said, frame by frame.
                update(walk.copy(captures = walk.captures.map { c ->
                    if (c.fromVideo == v.sha256 && c.atMs != null) c.copy(room = roomAt(c.atMs, mentions, v.room)) else c }))
                // Every item named aloud gets a frame from that moment: the nearest pulled frame,
                // or a new one, the sharpest within half a second.
                val items = mentions.filter { it.kind == "item" }
                val start = runCatching { Instant.parse(v.startedAt) }.getOrNull()
                val toRead = mutableListOf<String>()
                items.forEachIndexed { i, m ->
                    pullNote = "Finding the frame for \"${m.text}\" (${i + 1} of ${items.size})"
                    val said = Spotted(m.text, listOfNotNull(m.brandModel.takeIf { it.isNotBlank() },
                        m.purchaseYear?.let { y -> "bought $y" }, m.quantity?.let { q -> "qty $q" }).joinToString(", "), true)
                    val near = walk.captures.filter { it.fromVideo == v.sha256 && it.atMs != null }
                        .minByOrNull { kotlin.math.abs(it.atMs!! - m.atMs) }
                    if (near != null && kotlin.math.abs(near.atMs!! - m.atMs) <= 1500) {
                        replace(near.sha256) { it.copy(said = (it.said + said).distinct()) }
                    } else {
                        val fr = withContext(Dispatchers.IO) { pullFrameAt(store.video(v), store.dir, m.atMs) }
                        if (fr != null) {
                            val (sha, file) = withContext(Dispatchers.IO) { store.accept(fr.file) }
                            val existing = walk.captures.firstOrNull { it.sha256 == sha }
                            if (existing != null) replace(sha) { it.copy(said = (it.said + said).distinct()) }
                            else {
                                update(walk.copy(captures = walk.captures + Capture(
                                    sha256 = sha, file = file.name, room = roomAt(fr.ms, mentions, v.room),
                                    capturedAt = (start?.plusMillis(fr.ms) ?: Instant.now()).toString(),
                                    recordedAt = null, fromVideo = v.sha256, atMs = fr.ms, said = listOf(said))))
                                toRead += sha
                            }
                        }
                    }
                }
                toRead.forEachIndexed { i, sha ->
                    pullNote = "Reading named frame ${i + 1} of ${toRead.size}"
                    spotOne(sha)
                }
                pullNote = "Heard ${mentions.count { it.kind == "room" }} rooms and ${items.size} named items"
            } catch (e: Exception) {
                val msg = e.message ?: e.javaClass.simpleName
                update(walk.copy(videos = walk.videos.map { if (it.sha256 == v.sha256) it.copy(listenError = msg) else it }))
                pullNote = "Could not hear the narration: $msg"
            } finally {
                audio.delete() // a working copy only; the video keeps the original
                pulling = null
            }
        }
    }

    fun pull(v: VideoRecord) { scope.launch { doPull(v) } }
    fun listen(v: VideoRecord) { scope.launch { doListen(v) } }

    // Adding a video starts the whole walk processing at once: frames, Gemini Nano reads,
    // then the narration in the cloud. Nobody has to remember to come back and tap.
    fun processVideo(sha: String) {
        scope.launch {
            walk.videos.firstOrNull { it.sha256 == sha }?.let { doPull(it) }
            walk.videos.firstOrNull { it.sha256 == sha }?.let { doListen(it) }
        }
    }

    // Gemini Nano only runs while this app is on screen. Reads that were paused when the
    // adjuster switched away pick up again on return, in order, with nothing to re-tap.
    val onScreen by CaptureActivity.onScreen.collectAsState()
    LaunchedEffect(onScreen) {
        if (!onScreen || pulling != null) return@LaunchedEffect
        val waiting = walk.captures.filter { it.source == null && it.sha256 !in working &&
            (it.modelError == null || it.modelError.startsWith(PAUSED)) }
        waiting.forEachIndexed { i, c ->
            pullNote = "Picking up where it paused: frame ${i + 1} of ${waiting.size}"
            spotOne(c.sha256)
        }
        if (waiting.isNotEmpty()) pullNote = "All frames read"
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
                processVideo(sha)
            }
        }
    }

    // A walk recorded on Meta glasses, saved to this phone by the Meta AI app. It is copied in
    // byte for byte and hashed on receipt; its recorded date comes from the video's own metadata.
    fun importGlassesVideo(uri: Uri) {
        scope.launch {
            pullNote = "Copying the glasses video into the walk"
            val pending = store.newVideoFile()
            val name = runCatching {
                ctx.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)
                    ?.use { c -> if (c.moveToFirst()) c.getString(0) else null }
            }.getOrNull()
            val copied = withContext(Dispatchers.IO) {
                runCatching {
                    ctx.contentResolver.openInputStream(uri)!!.use { input ->
                        pending.outputStream().use { input.copyTo(it, 1 shl 20) }
                    }
                }.isSuccess
            }
            if (!copied || pending.length() == 0L) { pending.delete(); pullNote = "Could not read that video"; return@launch }
            val (sha, file) = withContext(Dispatchers.IO) { store.acceptVideo(pending) }
            val (recorded, ms) = withContext(Dispatchers.IO) { videoMeta(file) }
            if (walk.videos.none { it.sha256 == sha }) {
                update(walk.copy(videos = listOf(VideoRecord(sha, file.name, room.trim(),
                    startedAt = recorded ?: Instant.now().toString(), durationMs = ms,
                    provenance = "from_glasses", recordedAt = recorded, sourceName = name,
                    receivedAt = Instant.now().toString())) + walk.videos))
                processVideo(sha)
            }
            pullNote = ""
        }
    }
    val pickGlassesVideo = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) importGlassesVideo(uri)
    }
    // The day's videos, glasses first. Null while the list is closed.
    var todays by remember { mutableStateOf<List<PhoneVideo>?>(null) }
    fun loadTodays() {
        scope.launch { todays = withContext(Dispatchers.IO) { runCatching { todaysVideos(ctx) }.getOrDefault(emptyList()) } }
    }
    val videoPermission = if (Build.VERSION.SDK_INT >= 33) android.Manifest.permission.READ_MEDIA_VIDEO
        else android.Manifest.permission.READ_EXTERNAL_STORAGE
    val askVideoPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) loadTodays()
        else pickGlassesVideo.launch(androidx.activity.result.PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.VideoOnly))
    }
    fun openGlasses() {
        if (ctx.checkSelfPermission(videoPermission) == android.content.pm.PackageManager.PERMISSION_GRANTED) loadTodays()
        else askVideoPermission.launch(videoPermission)
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

    val blocker = when {
        walk.claimNumber.isBlank() -> "Enter the claim number first"
        room.isBlank() -> "Choose a room first"
        else -> null
    }
    val ready = blocker == null
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
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("Claim", fontWeight = FontWeight.SemiBold)
                        OutlinedTextField(walk.claimNumber, { update(walk.copy(claimNumber = it)) },
                            label = { Text("Claim number") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(walk.lossAddress, { update(walk.copy(lossAddress = it)) },
                            label = { Text("Loss address") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                        Text("Every photo, frame and video in this walk is filed under this claim.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
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
                    Button(onClick = { cameraOpen = true }, enabled = ready, modifier = Modifier.weight(1f)) {
                        Text(blocker ?: "Open camera")
                    }
                    OutlinedButton(onClick = { share() }, enabled = walk.captures.isNotEmpty() || walk.videos.isNotEmpty()) { Text("Share walk") }
                }
            }
            item {
                OutlinedButton(onClick = { openGlasses() }, enabled = ready, modifier = Modifier.fillMaxWidth()) {
                    Text(blocker ?: "Add a walk recorded on Meta glasses")
                }
                Text("Add the video exactly as it came off the glasses. Never trim or edit it first: it is " +
                    "evidence. The original also stays in the phone's gallery.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            todays?.let { list ->
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("Videos from today", fontWeight = FontWeight.SemiBold)
                            if (list.isEmpty()) Text("No videos taken today were found on this phone. If the Meta AI app has " +
                                "not saved the walk to the gallery yet, do that first.", style = MaterialTheme.typography.bodySmall)
                            val fmt = java.time.format.DateTimeFormatter.ofPattern("h:mm a").withZone(java.time.ZoneId.systemDefault())
                            list.forEach { pv ->
                                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Column(Modifier.weight(1f)) {
                                        Text("${fmt.format(Instant.ofEpochMilli(pv.takenMs))} · " +
                                            "${pv.durationMs / 60000}:${"%02d".format((pv.durationMs / 1000) % 60)}" +
                                            if (pv.fromGlasses) " · Meta glasses" else "", style = MaterialTheme.typography.bodyMedium)
                                        Text("${pv.folder} · ${pv.name}", style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                    Button(onClick = { todays = null; importGlassesVideo(pv.uri) }) { Text("Add") }
                                }
                            }
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                TextButton(onClick = {
                                    todays = null
                                    pickGlassesVideo.launch(androidx.activity.result.PickVisualMediaRequest(
                                        ActivityResultContracts.PickVisualMedia.VideoOnly))
                                }) { Text("Choose from all videos") }
                                TextButton(onClick = { todays = null }) { Text("Close") }
                            }
                        }
                    }
                }
            }
            items(walk.videos, key = { it.sha256 }) { v ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text("Walk video · ${v.room}", fontWeight = FontWeight.SemiBold)
                        val len = "${v.durationMs / 60000}:${"%02d".format((v.durationMs / 1000) % 60)}"
                        Text(if (v.provenance == "from_glasses")
                                "Received from glasses · recorded ${v.recordedAt?.take(16)?.replace('T', ' ')?.plus(" UTC") ?: "date not in the file"} · $len"
                            else "Captured in app · ${v.startedAt.take(16).replace('T', ' ')} UTC · $len",
                            style = MaterialTheme.typography.bodySmall)
                        if (v.provenance == "from_glasses") Text(
                            "Hashed when this phone received it${v.sourceName?.let { " ($it)" } ?: ""}, not when the glasses recorded it.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        recordedGap(v)?.let { days -> Text(
                            "Recorded $days ${if (days == 1L) "day" else "days"} before it was added to this walk. " +
                                "Adjuster to confirm it belongs to this claim.",
                            style = MaterialTheme.typography.bodySmall, color = WARN) }
                        Text("SHA-256 ${v.sha256.take(16)}…", fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("Kept unchanged for review. Frames are copies; the video is never edited.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        val frameCount = walk.captures.count { it.fromVideo == v.sha256 }
                        if (pulling == v.sha256) Text(pullNote, style = MaterialTheme.typography.bodySmall)
                        else {
                            if (frameCount > 0) Text("$frameCount frames from this video", style = MaterialTheme.typography.bodySmall)
                            if (pullNote.isNotEmpty()) Text(pullNote, style = MaterialTheme.typography.bodySmall)
                            Row(horizontalArrangement = Arrangement.spacedBy(6.dp),
                                modifier = Modifier.horizontalScroll(rememberScrollState())) {
                                OutlinedButton(onClick = { pull(v) }, enabled = pulling == null) {
                                    Text(if (frameCount == 0) "Pull frames" else "Pull again")
                                }
                                if (frameCount > 0) OutlinedButton(onClick = {
                                    scope.launch {
                                        pulling = v.sha256
                                        val shas = walk.captures.filter { it.fromVideo == v.sha256 }.map { it.sha256 }
                                        shas.forEachIndexed { i, sha -> pullNote = "Reading frame ${i + 1} of ${shas.size} again"; spotOne(sha) }
                                        pullNote = "${shas.size} frames read again"
                                        pulling = null
                                    }
                                }, enabled = pulling == null) { Text("Read again") }
                                OutlinedButton(onClick = { listen(v) }, enabled = pulling == null) {
                                    Text(if (v.mentions == null) "Hear narration" else "Hear again")
                                }
                            }
                            v.listenError?.let { Text("Narration not heard yet: $it", color = WARN, style = MaterialTheme.typography.bodySmall) }
                            v.mentions?.let { ms ->
                                Text(if (ms.isEmpty()) "Narration heard in the cloud: nothing named."
                                    else "Narration heard in the cloud: " + ms.joinToString(" · ") { m ->
                                        "${fmt(m.atMs)} " + (if (m.kind == "room") "[${canonicalRoom(m.text)}]" else m.text) +
                                            (if (m.brandModel.isNotBlank()) " (${m.brandModel})" else "") +
                                            (m.purchaseYear?.let { y -> ", bought $y" } ?: "") },
                                    style = MaterialTheme.typography.bodySmall)
                            }
                            v.transcript?.let { t ->
                                Text("Heard, word for word: $t", style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
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
                        val toConfirm = inventory.count { it.maybeOnScreen }
                        Text("${inventory.size} belongings${if (toConfirm > 0) " ($toConfirm to confirm)" else ""} from ${walk.captures.count { it.source != null }} photos and frames. " +
                            "Repeats are rolled up, never dropped; each line keeps every sighting.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        inventory.groupBy { it.room }.forEach { (r, lines) ->
                            Text(r, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 4.dp))
                            // What code kept off the list in this room, and why: shown, never silently dropped.
                            val inRoom = walk.captures.filter { it.room.equals(r, ignoreCase = true) }
                            val aside = (inRoom.flatMap { c -> c.setAside + c.items.filter { isStructure(it.name) }.map { it.name } }
                                .map { withoutBrackets(it).lowercase() }.groupingBy { it }.eachCount() to
                                inRoom.flatMap { c -> c.notProperty + c.items.filter { isNotProperty(it.name) }.map { it.name } }
                                .map { withoutBrackets(it).lowercase() }.groupingBy { it }.eachCount())
                            fun fmtAside(m: Map<String, Int>) = m.entries.sortedByDescending { it.value }
                                .joinToString(", ") { (k, n) -> if (n > 1) "$k ×$n" else k }
                            if (aside.first.isNotEmpty()) Text("   Set aside as part of the building: ${fmtAside(aside.first)}",
                                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            if (aside.second.isNotEmpty()) Text("   Set aside, not property: ${fmtAside(aside.second)}",
                                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            lines.forEach { ln ->
                                val times = ln.sightings.mapNotNull { it.atMs }.sorted()
                                val span = if (times.isNotEmpty()) ", ${fmt(times.first())} to ${fmt(times.last())}" else ""
                                Text(buildString {
                                    append("• ").append(ln.name)
                                    append("  (seen ${ln.sightings.size}×$span")
                                    if (ln.clearCount < ln.sightings.size) append("; ${ln.clearCount} clear")
                                    append(")")
                                }, style = MaterialTheme.typography.bodySmall)
                                if (ln.maybeOnScreen) Text("   may be on the TV screen, not in the room; adjuster to confirm",
                                    style = MaterialTheme.typography.labelSmall, color = WARN)
                                if (ln.said.isNotEmpty()) Text("   said: " + ln.said.distinct().joinToString("; "),
                                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                                // Named at more than one moment: maybe one item, maybe two. The adjuster decides.
                                if (ln.said.distinct().size >= 2) Text("   named ${ln.said.distinct().size} times; adjuster to confirm whether this is one item or ${ln.said.distinct().size}",
                                    style = MaterialTheme.typography.labelSmall, color = WARN)
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
                CaptureCard(c, store.photo(c), c.sha256 in working, onCloud = { look(c.sha256, cloud = true) }) { look(c.sha256) }
            }
            item {
                val unread = walk.captures.count { it.fromVideo != null && it.source == null &&
                    (it.modelError == null || it.modelError.startsWith(PAUSED)) }
                val unheard = walk.videos.filter { it.mentions == null }
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("Finish this walk", fontWeight = FontWeight.SemiBold)
                        when {
                            walk.videos.isEmpty() -> Text("Add this walk's video before starting another walk: record it " +
                                "in the app, or add the one from the glasses now.", style = MaterialTheme.typography.bodySmall)
                            pulling != null || walk.videos.any { v -> walk.captures.none { it.fromVideo == v.sha256 } } ->
                                Text("Pulling and reading frames. Keep the app open; it pauses if you switch away and picks up when you return.",
                                    style = MaterialTheme.typography.bodySmall)
                            unread > 0 -> Text("$unread frames still to read on the phone. Keep the app open.",
                                style = MaterialTheme.typography.bodySmall)
                            unheard.isNotEmpty() -> {
                                Text("Every frame is read. The narration still needs to be heard, which needs signal.",
                                    style = MaterialTheme.typography.bodySmall)
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Button(onClick = { unheard.forEach { listen(it) } }) { Text("Hear narration now") }
                                    TextButton(onClick = {
                                        update(walk.copy(videos = walk.videos.map { v -> if (v.mentions == null) v.copy(
                                            listenError = "walk closed at ${Instant.now()} without the narration heard: no signal") else v }))
                                        walk = store.start("", "${Build.MANUFACTURER} ${Build.MODEL}")
                                    }) { Text("No signal: close without it") }
                                }
                            }
                            else -> {
                                Text("Video added, every frame read, narration heard. This walk is complete.",
                                    style = MaterialTheme.typography.bodySmall)
                                Button(onClick = { walk = store.start("", "${Build.MANUFACTURER} ${Build.MODEL}") }) {
                                    Text("Start a new walk (this one is kept on the phone)")
                                }
                            }
                        }
                    }
                }
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
fun CaptureCard(c: Capture, file: File, busy: Boolean, onCloud: () -> Unit = {}, onRetry: () -> Unit) {
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
                        TextButton(onClick = onCloud) { Text("Read in the cloud") }
                        Text("Not read yet: ${c.modelError}", color = WARN, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = onRetry) { Text("Look again") }
                    }
                    c.items.isEmpty() && c.source != null -> {
                        Text("No household items named.", style = MaterialTheme.typography.bodySmall)
                        if (!c.raw.isNullOrBlank()) Text("Model said: ${c.raw}", fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        TextButton(onClick = onRetry) { Text("Look again") }
                    }
                    // The model sometimes repeats one name in a frame; show it once with the count.
                    else -> c.items.groupBy { listOf(it.name.lowercase(), it.brandModel, it.clear) }.values.forEach { same ->
                        val it2 = same.first()
                        Text(
                            buildString {
                                append("• ").append(it2.name)
                                if (same.size > 1) append(" ×").append(same.size).append(" (named ").append(same.size).append(" times)")
                                if (it2.brandModel.isNotBlank()) append(" (").append(it2.brandModel).append(")")
                                if (!it2.clear) append("  [unclear frame]")
                            },
                            style = MaterialTheme.typography.bodySmall,
                            color = if (it2.clear) MaterialTheme.colorScheme.onSurface else WARN,
                        )
                    }
                }
                if (c.said.isNotEmpty()) {
                    Text("Named aloud here: " + c.said.joinToString(", ") { it.name + if (it.brandModel.isNotBlank()) " (${it.brandModel})" else "" },
                        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
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

/** Whole days between a glasses video's recorded date and when it was added; null when under a day or unknown. */
fun recordedGap(v: VideoRecord): Long? {
    val rec = v.recordedAt?.let { runCatching { Instant.parse(it) }.getOrNull() } ?: return null
    val got = v.receivedAt?.let { runCatching { Instant.parse(it) }.getOrNull() } ?: return null
    val days = java.time.Duration.between(rec, got).toDays()
    return days.takeIf { it >= 1 }
}

/** Marks a frame read that stopped because the app left the screen; it is retried on return. */
const val PAUSED = "paused:"
