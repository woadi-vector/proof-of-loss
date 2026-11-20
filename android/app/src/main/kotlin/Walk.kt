package com.woadivector.proofofloss

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.time.Instant
import java.util.Locale
import java.util.UUID

/** One item the model named in a photo. The model names things; it never prices or judges them. */
data class Spotted(val name: String, val brandModel: String, val clear: Boolean)

/**
 * One photo taken in the app. The SHA-256 is of the original bytes, computed the moment the
 * camera hands the file back, and the file is never modified afterwards. [capturedAt] is this
 * phone's clock at that moment; [recordedAt] is the date inside the photo's own metadata, if any.
 */
data class Capture(
    val sha256: String,
    val file: String,
    val room: String,
    val capturedAt: String,
    val recordedAt: String?,
    val items: List<Spotted> = emptyList(),
    val source: String? = null, // "on_device" or "in_cloud": where the model actually ran
    val modelMs: Long? = null,
    val modelError: String? = null,
    val raw: String? = null, // the model's own words when nothing could be read from them
    val setAside: List<String> = emptyList(), // named by the model, but part of the building
    val notProperty: List<String> = emptyList(), // people and animals: not contents
)

/** A walk video recorded in the app: hashed when recording stops, never modified. */
data class VideoRecord(
    val sha256: String,
    val file: String,
    val room: String, // the room selected when recording started; narration names the rest
    val startedAt: String,
    val durationMs: Long,
)

data class Walk(
    val walkId: String,
    val label: String,
    val startedAt: String,
    val device: String,
    val captures: List<Capture>,
    val videos: List<VideoRecord> = emptyList(),
) {
    fun toJson(): JSONObject = JSONObject()
        .put("format", "pol-walk-1")
        .put("walk_id", walkId)
        .put("label", label)
        .put("started_at", startedAt)
        .put("device", device)
        .put("photos", JSONArray().apply {
            captures.forEach { c ->
                put(JSONObject()
                    .put("file", c.file)
                    .put("sha256", c.sha256)
                    .put("room", c.room)
                    .put("captured_at", c.capturedAt)
                    .put("recorded_at", c.recordedAt ?: JSONObject.NULL)
                    .put("provenance", "captured_in_app")
                    .put("source", c.source ?: JSONObject.NULL)
                    .put("model_ms", c.modelMs ?: JSONObject.NULL)
                    .put("model_error", c.modelError ?: JSONObject.NULL)
                    .put("raw", c.raw ?: JSONObject.NULL)
                    .put("set_aside_as_structure", JSONArray(c.setAside))
                    .put("set_aside_not_property", JSONArray(c.notProperty))
                    .put("items", JSONArray().apply {
                        c.items.forEach {
                            put(JSONObject().put("name", it.name).put("brand_model", it.brandModel)
                                .put("clear", it.clear))
                        }
                    }))
            }
        })
        .put("videos", JSONArray().apply {
            videos.forEach { v ->
                put(JSONObject().put("file", v.file).put("sha256", v.sha256).put("room", v.room)
                    .put("started_at", v.startedAt).put("duration_ms", v.durationMs)
                    .put("provenance", "captured_in_app"))
            }
        })

    companion object {
        fun fromJson(o: JSONObject): Walk {
            val photos = o.optJSONArray("photos") ?: JSONArray()
            val caps = (0 until photos.length()).map { i ->
                val p = photos.getJSONObject(i)
                val items = p.optJSONArray("items") ?: JSONArray()
                Capture(
                    sha256 = p.getString("sha256"),
                    file = p.getString("file"),
                    room = p.optString("room"),
                    capturedAt = p.optString("captured_at"),
                    recordedAt = p.optStringOrNull("recorded_at"),
                    items = (0 until items.length()).map { j ->
                        val it = items.getJSONObject(j)
                        Spotted(it.optString("name"), it.optString("brand_model"), it.optBoolean("clear"))
                    },
                    source = p.optStringOrNull("source"),
                    modelMs = if (p.isNull("model_ms")) null else p.optLong("model_ms"),
                    modelError = p.optStringOrNull("model_error"),
                    raw = p.optStringOrNull("raw"),
                    setAside = p.optJSONArray("set_aside_as_structure")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList(),
                    notProperty = p.optJSONArray("set_aside_not_property")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList(),
                )
            }
            val vids = o.optJSONArray("videos") ?: JSONArray()
            val videos = (0 until vids.length()).map { i ->
                val v = vids.getJSONObject(i)
                VideoRecord(v.getString("sha256"), v.getString("file"), v.optString("room"),
                    v.optString("started_at"), v.optLong("duration_ms"))
            }
            return Walk(o.getString("walk_id"), o.optString("label"), o.optString("started_at"),
                o.optString("device"), caps, videos)
        }

        private fun JSONObject.optStringOrNull(k: String): String? =
            if (!has(k) || isNull(k)) null else optString(k)
    }
}

/** The current walk lives in the app's private storage until the adjuster shares it. */
class WalkStore(root: File) {
    val dir = File(root, "walks/current").apply { mkdirs() }
    private val record = File(dir, "walk.json")

    fun load(): Walk? = runCatching { Walk.fromJson(JSONObject(record.readText())) }.getOrNull()

    fun save(w: Walk) {
        val tmp = File(dir, "walk.json.tmp")
        tmp.writeText(w.toJson().toString(2))
        tmp.renameTo(record)
    }

    fun start(label: String, device: String): Walk {
        // Keep the previous walk: move it aside, never delete evidence.
        if (record.exists()) {
            val old = File(dir.parentFile, "walk_" + System.currentTimeMillis())
            dir.renameTo(old)
            dir.mkdirs()
        }
        val w = Walk(UUID.randomUUID().toString(), label, Instant.now().toString(), device, emptyList())
        save(w)
        return w
    }

    fun newPhotoFile(): File = File(dir, "pending_${System.currentTimeMillis()}.jpg")

    fun newVideoFile(): File = File(dir, "pending_${System.currentTimeMillis()}.mp4")

    fun video(v: VideoRecord): File = File(dir, v.file)

    /** Hash a finished walk video, then name it by its hash. */
    fun acceptVideo(pending: File): Pair<String, File> {
        val sha = sha256(pending)
        val dest = File(dir, "$sha.mp4")
        if (dest.exists()) pending.delete() else pending.renameTo(dest)
        return sha to dest
    }

    fun photo(c: Capture): File = File(dir, c.file)

    /** Hash the camera's file, then name it by its hash. Same bytes, same name. */
    fun accept(pending: File): Pair<String, File> {
        val sha = sha256(pending)
        val dest = File(dir, "$sha.jpg")
        if (dest.exists()) pending.delete() else pending.renameTo(dest)
        return sha to dest
    }

    fun recordFile(): File = record
}

fun sha256(f: File): String {
    val md = MessageDigest.getInstance("SHA-256")
    f.inputStream().use { input ->
        val buf = ByteArray(1 shl 16)
        while (true) {
            val n = input.read(buf)
            if (n <= 0) break
            md.update(buf, 0, n)
        }
    }
    return md.digest().joinToString("") { "%02x".format(it) }
}

/** The date the camera wrote into the photo itself (EXIF), or null. Never the file system's time. */
fun exifDate(f: File): String? = runCatching {
    val raw = ExifInterface(f.absolutePath).getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL) ?: return null
    val d = SimpleDateFormat("yyyy:MM:dd HH:mm:ss", Locale.US).parse(raw) ?: return null
    SimpleDateFormat("yyyy-MM-dd", Locale.US).format(d)
}.getOrNull()

/** A downscaled, upright copy for the model and the screen. The original file is not touched. */
fun loadScaled(f: File, maxSide: Int): Bitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(f.absolutePath, bounds)
    if (bounds.outWidth <= 0) return null
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= maxSide) sample *= 2
    val bmp = BitmapFactory.decodeFile(f.absolutePath, BitmapFactory.Options().apply { inSampleSize = sample })
        ?: return null
    val rotation = runCatching {
        when (ExifInterface(f.absolutePath).getAttributeInt(ExifInterface.TAG_ORIENTATION, 1)) {
            ExifInterface.ORIENTATION_ROTATE_90 -> 90f
            ExifInterface.ORIENTATION_ROTATE_180 -> 180f
            ExifInterface.ORIENTATION_ROTATE_270 -> 270f
            else -> 0f
        }
    }.getOrDefault(0f)
    val scale = minOf(1f, maxSide.toFloat() / maxOf(bmp.width, bmp.height))
    if (rotation == 0f && scale >= 1f) return bmp
    val m = Matrix().apply { postScale(scale, scale); postRotate(rotation) }
    return Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
}

/**
 * Parts of the building are the structure claim, not contents. The model is told not to list
 * them, but code makes the call: anything whose name is a building part is set aside and shown
 * as set aside, never silently dropped.
 */
private val STRUCTURE = setOf(
    "wall", "walls", "door", "doors", "doorway", "window", "windows", "floor", "flooring", "ceiling",
    "vent", "air vent", "register", "outlet", "light switch", "switch", "baseboard", "trim", "molding",
    "closet door", "closet doors", "carpet", "hardwood floor", "tile", "drywall", "stairs", "railing",
    "ceiling fan", "smoke detector", "thermostat",
)

fun isStructure(name: String): Boolean =
    // "storage cabinet/closet doors": set aside only if every part is a building part.
    name.split('/', ',', '&').map { it.trim() }.filter { it.isNotEmpty() }.let { parts ->
        parts.isNotEmpty() && parts.all { isStructurePart(it) }
    }

private fun isStructurePart(name: String): Boolean {
    val n = name.lowercase().replace(Regex("[^a-z ]"), " ").replace(Regex("\\s+"), " ").trim()
    if (n in STRUCTURE) return true
    val last = n.substringAfterLast(' ')
    return last in setOf("wall", "door", "doors", "window", "windows", "floor", "ceiling", "vent", "outlet", "baseboard", "trim")
}

private val NOT_PROPERTY = setOf(
    "dog", "dogs", "puppy", "cat", "cats", "kitten", "pet", "pets", "person", "people", "man", "woman",
    "child", "children", "kid", "baby", "bird", "fish", "hamster", "rabbit",
)

/** People and animals are not contents. Set aside and shown, like building parts. */
fun isNotProperty(name: String): Boolean =
    name.lowercase().replace(Regex("[^a-z ]"), " ").trim().split(Regex("\\s+")).lastOrNull() in NOT_PROPERTY
