package com.woadivector.proofofloss

import android.graphics.Bitmap
import android.graphics.Color
import android.media.MediaMetadataRetriever
import android.os.Build
import java.io.File
import kotlin.math.abs

/**
 * The frame puller (the Editor, in Dailies terms): plain code, no model calls.
 *
 * Samples the walk video, keeps a frame wherever the scene has changed enough since the last
 * kept frame, and for each one takes the sharpest of three nearby frames. The video itself is
 * only read, never changed. Every kept frame records the moment it came from.
 */
data class PulledFrame(val ms: Long, val file: File, val sharpness: Double)

private const val SAMPLE_MS = 500L
private const val MIN_GAP_MS = 2500L
private const val CHANGE_THRESHOLD = 32.0 // mean gray difference (0..255) on a 32x18 thumbnail

fun pullFrames(
    video: File,
    outDir: File,
    maxFrames: Int = 12,
    progress: (String) -> Unit = {},
): List<PulledFrame> {
    val r = MediaMetadataRetriever()
    try {
        r.setDataSource(video.absolutePath)
        val duration = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: return emptyList()

        // 1. Where does the scene change? Compare small gray thumbnails every half second.
        val picks = mutableListOf<Long>()
        var lastKept: IntArray? = null
        var lastKeptMs = -MIN_GAP_MS
        var t = 0L
        while (t < duration) {
            progress("Scanning the video: ${t / 1000}s of ${duration / 1000}s")
            val thumb = r.frame(t, 32, 18)?.let { gray(it) }
            if (thumb != null) {
                val changed = lastKept == null || meanDiff(thumb, lastKept) > CHANGE_THRESHOLD
                if (changed && t - lastKeptMs >= MIN_GAP_MS) {
                    picks += t
                    lastKept = thumb
                    lastKeptMs = t
                }
            }
            t += SAMPLE_MS
        }
        // Too many changes (a fast walk): keep an even spread, never more than maxFrames.
        val chosen = if (picks.size <= maxFrames) picks
        else (0 until maxFrames).map { picks[it * picks.size / maxFrames] }

        // 2. For each moment, the sharpest of three nearby frames.
        val out = mutableListOf<PulledFrame>()
        chosen.forEachIndexed { i, ms ->
            progress("Choosing the sharpest frame ${i + 1} of ${chosen.size}")
            val best = listOf(ms - 300, ms, ms + 300).filter { it in 0 until duration }
                .mapNotNull { at -> r.frame(at, 320, 180)?.let { at to sharpness(it) } }
                .maxByOrNull { it.second } ?: return@forEachIndexed
            val full = r.frame(best.first, 1280, 720) ?: return@forEachIndexed
            val f = File(outDir, "pending_frame_${best.first}.jpg")
            f.outputStream().use { full.compress(Bitmap.CompressFormat.JPEG, 90, it) }
            out += PulledFrame(best.first, f, best.second)
        }
        return out
    } finally {
        r.release()
    }
}

private fun MediaMetadataRetriever.frame(ms: Long, w: Int, h: Int): Bitmap? = runCatching {
    val us = ms * 1000
    if (Build.VERSION.SDK_INT >= 27) getScaledFrameAtTime(us, MediaMetadataRetriever.OPTION_CLOSEST, w, h)
    else getFrameAtTime(us, MediaMetadataRetriever.OPTION_CLOSEST)?.let { Bitmap.createScaledBitmap(it, w, h, true) }
}.getOrNull()

private fun gray(b: Bitmap): IntArray {
    val small = if (b.width == 32 && b.height == 18) b else Bitmap.createScaledBitmap(b, 32, 18, true)
    val px = IntArray(32 * 18)
    small.getPixels(px, 0, 32, 0, 0, 32, 18)
    return IntArray(px.size) { (Color.red(px[it]) * 299 + Color.green(px[it]) * 587 + Color.blue(px[it]) * 114) / 1000 }
}

private fun meanDiff(a: IntArray, b: IntArray): Double = a.indices.sumOf { abs(a[it] - b[it]).toDouble() } / a.size

/** Variance of a simple Laplacian on gray pixels: higher means sharper. */
private fun sharpness(b: Bitmap): Double {
    val w = b.width
    val h = b.height
    val px = IntArray(w * h)
    b.getPixels(px, 0, w, 0, 0, w, h)
    val g = IntArray(px.size) { (Color.red(px[it]) * 299 + Color.green(px[it]) * 587 + Color.blue(px[it]) * 114) / 1000 }
    var sum = 0.0
    var sq = 0.0
    var n = 0
    for (y in 1 until h - 1) for (x in 1 until w - 1) {
        val i = y * w + x
        val lap = (4 * g[i] - g[i - 1] - g[i + 1] - g[i - w] - g[i + w]).toDouble()
        sum += lap; sq += lap * lap; n++
    }
    if (n == 0) return 0.0
    val mean = sum / n
    return sq / n - mean * mean
}
