package com.woadivector.proofofloss

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import com.google.firebase.Firebase
import com.google.firebase.ai.ai
import com.google.firebase.ai.type.GenerativeBackend
import com.google.firebase.ai.type.Schema
import com.google.firebase.ai.type.content
import com.google.firebase.ai.type.generationConfig
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer

/** One thing the adjuster said on the walk, at a moment in the video. */
data class Mention(
    val atMs: Long,
    val kind: String, // "room" or "item"
    val text: String,
    val brandModel: String = "",
    val purchaseYear: Int? = null,
    val quantity: Int? = null,
)

/**
 * Copies the audio track out of the walk video into its own file, sample for sample. No
 * re-encoding: the narration sent for transcription is exactly what the video holds.
 */
fun extractAudio(video: File, out: File): Boolean {
    val ex = MediaExtractor()
    try {
        ex.setDataSource(video.absolutePath)
        val track = (0 until ex.trackCount).firstOrNull {
            ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
        } ?: return false
        ex.selectTrack(track)
        val format = ex.getTrackFormat(track)
        val mux = MediaMuxer(out.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        val dst = mux.addTrack(format)
        mux.start()
        val buf = ByteBuffer.allocate(1 shl 20)
        val info = MediaCodec.BufferInfo()
        while (true) {
            info.size = ex.readSampleData(buf, 0)
            if (info.size < 0) break
            info.offset = 0
            info.presentationTimeUs = ex.sampleTime
            info.flags = if (ex.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0
            mux.writeSampleData(dst, buf, info)
            ex.advance()
        }
        mux.stop()
        mux.release()
        return true
    } finally {
        ex.release()
    }
}

private const val LISTEN_PROMPT = """This is the audio from an insurance adjuster's walkthrough video of a home.
First, in transcript, write down every word you can hear, in order, with the time at the start
of each sentence as a plain count of seconds from the start, like "[3] This is the office.
[66] That's my ProForm bike." (66 is one minute six seconds; never write it as 106.)
Write "(no speech)" if there is none and "(unclear)" for words you cannot make out.
Then list, in mentions, what the adjuster names, in order, with the time it is said:
- kind "room": a room they are entering or standing in ("kitchen", "primary bedroom").
- kind "item": any belonging, object or piece of property they mention, even in passing
  ("that's the TV", "my exercise bike", "the dresser came from my mom"). Put the item in text,
  any brand or model they say in brand_model, any purchase year they say in purchase_year,
  and any count they say in quantity.
Use only what is actually said. Do not add items, brands, years or counts that are not spoken.
Use "" for brand_model and 0 for purchase_year or quantity when not said."""

/**
 * Listening is done by cloud Gemini through Firebase AI Logic: the on-device model reads
 * images, not audio. It runs when the phone has signal; seeing never waits for it.
 */
class Listener {
    private val model = Firebase.ai(backend = GenerativeBackend.googleAI()).generativeModel(
        modelName = CLOUD_MODEL,
        generationConfig = generationConfig {
            responseMimeType = "application/json"
            responseSchema = Schema.obj(
                properties = mapOf(
                    "transcript" to Schema.string(),
                    "mentions" to Schema.array(
                        items = Schema.obj(
                            properties = mapOf(
                                "t" to Schema.double(),
                                "kind" to Schema.string(),
                                "text" to Schema.string(),
                                "brand_model" to Schema.string(),
                                "purchase_year" to Schema.integer(),
                                "quantity" to Schema.integer(),
                            )
                        )
                    )
                )
            )
        },
    )

    suspend fun listen(audio: File, durationMs: Long): Heard {
        val bytes = audio.readBytes()
        val response = model.generateContent(content {
            inlineData(bytes, "audio/mp4")
            text(LISTEN_PROMPT)
        })
        val text = response.text ?: ""
        return Heard(parseTranscript(text), parseMentions(text, durationMs))
    }
}

/** What the cloud heard: the words as transcribed, and the rooms and items named in them. */
data class Heard(val transcript: String, val mentions: List<Mention>)

/** The transcript as the model wrote it; if it wrote none, the start of its raw answer. */
fun parseTranscript(text: String): String {
    val t = text.trim().removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
    return runCatching { JSONObject(t.substring(t.indexOf('{'), t.lastIndexOf('}') + 1)).optString("transcript") }
        .getOrNull()?.trim()?.takeIf { it.isNotEmpty() } ?: "(no transcript returned) " + t.take(300)
}

/**
 * Models sometimes write 1:06 as 106. If any time runs past the end of the video and every such
 * time reads as minutes and seconds that fit inside it, the answer used that style, so every
 * time of 100 or more is read that way. Otherwise the times are left as written.
 */
fun fixClock(times: List<Double>, durationMs: Long): List<Double> {
    if (durationMs <= 0) return times
    fun asClock(t: Double): Double? {
        val whole = t.toLong()
        val s = whole % 100
        val c = (whole / 100) * 60 + s + (t - whole)
        return if (whole >= 100 && s < 60 && c * 1000 <= durationMs) c else null
    }
    val past = times.filter { it * 1000 > durationMs }
    if (past.isEmpty() || past.any { asClock(it) == null }) return times
    return times.map { asClock(it) ?: it }
}

fun parseMentions(text: String, durationMs: Long = 0): List<Mention> {
    val t = text.trim().removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
    val arr = runCatching { JSONObject(t.substring(t.indexOf('{'), t.lastIndexOf('}') + 1)).optJSONArray("mentions") }
        .getOrNull() ?: return emptyList()
    val objs = (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    val times = fixClock(objs.map { it.optDouble("t", 0.0) }, durationMs)
    return objs.indices.mapNotNull { i ->
        val o = objs[i]
        val said = o.optString("text").trim()
        val kind = o.optString("kind").lowercase().let { if (it == "room") "room" else "item" }
        if (said.isEmpty()) null
        else Mention(
            atMs = (times[i] * 1000).toLong().coerceAtLeast(0),
            kind = kind,
            text = said,
            brandModel = o.optString("brand_model").trim(),
            purchaseYear = o.optInt("purchase_year", 0).takeIf { it in 1950..2100 },
            quantity = o.optInt("quantity", 0).takeIf { it > 0 },
        )
    }.sortedBy { it.atMs }
}

/** Which room the adjuster was in at a moment: the last room named before it, else the starting room. */
fun roomAt(ms: Long, mentions: List<Mention>, startRoom: String): String =
    mentions.lastOrNull { it.kind == "room" && it.atMs <= ms }?.text?.let { canonicalRoom(it) } ?: startRoom

/** "the kitchen" -> "Kitchen", matched to the standard list when it is one. */
fun canonicalRoom(said: String): String {
    val s = said.trim().removePrefix("the ").removePrefix("The ").trim()
    return STANDARD_ROOMS.firstOrNull { it.equals(s, ignoreCase = true) } ?: s.replaceFirstChar { it.uppercase() }
}
