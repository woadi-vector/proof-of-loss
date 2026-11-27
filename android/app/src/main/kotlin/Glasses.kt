package com.woadivector.proofofloss

import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import java.time.LocalDate
import java.time.ZoneId

/** A video on this phone the adjuster may add to the walk. Read only; nothing here changes it. */
data class PhoneVideo(
    val uri: Uri,
    val name: String,
    val folder: String,
    val takenMs: Long, // when the phone's media index says it was taken
    val durationMs: Long,
    val fromGlasses: Boolean,
)

/**
 * Videos taken since the start of today, newest first. Ones in a folder the Meta AI app writes
 * to are marked as from the glasses, so the adjuster sees those first; the rest stay listed,
 * because a folder name is a hint, not proof.
 */
fun todaysVideos(ctx: Context): List<PhoneVideo> {
    val since = LocalDate.now().atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli()
    val collection = MediaStore.Video.Media.EXTERNAL_CONTENT_URI
    val cols = arrayOf(
        MediaStore.Video.Media._ID,
        MediaStore.Video.Media.DISPLAY_NAME,
        MediaStore.Video.Media.DATE_TAKEN,
        MediaStore.Video.Media.DATE_ADDED,
        MediaStore.Video.Media.DURATION,
        MediaStore.Video.Media.BUCKET_DISPLAY_NAME,
        MediaStore.Video.Media.RELATIVE_PATH,
    )
    val out = mutableListOf<PhoneVideo>()
    ctx.contentResolver.query(
        collection, cols,
        "(${MediaStore.Video.Media.DATE_TAKEN} >= ? OR ${MediaStore.Video.Media.DATE_ADDED} >= ?)",
        arrayOf(since.toString(), (since / 1000).toString()),
        "${MediaStore.Video.Media.DATE_TAKEN} DESC",
    )?.use { c ->
        while (c.moveToNext()) {
            val id = c.getLong(0)
            val taken = c.getLong(2).takeIf { it > 0 } ?: (c.getLong(3) * 1000)
            val folder = c.getString(5) ?: ""
            val path = c.getString(6) ?: ""
            out += PhoneVideo(
                uri = ContentUris.withAppendedId(collection, id),
                name = c.getString(1) ?: "video",
                folder = folder,
                takenMs = taken,
                durationMs = c.getLong(4),
                fromGlasses = isGlassesFolder("$path/$folder"),
            )
        }
    }
    return out.sortedWith(compareBy<PhoneVideo>({ !it.fromGlasses }, { -it.takenMs }))
}

/** Folders the Meta AI app (formerly Meta View) saves glasses media into. */
fun isGlassesFolder(path: String): Boolean {
    val p = path.lowercase()
    return "meta ai" in p || "meta view" in p || "metaai" in p || "ray-ban" in p || "rayban" in p
}
