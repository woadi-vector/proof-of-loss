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
List what the adjuster says, in order, with the time it is said (seconds from the start).
- kind "room": the adjuster names the room they are entering or standing in ("kitchen", "primary bedroom").
- kind "item": the adjuster names a belonging. Put the item in text ("65 inch television"),
  any brand or model they say in brand_model, any purchase year they say in purchase_year,
  and any count they say in quantity.
Use only what is actually said. Do not add items, brands, years or counts that are not spoken.
Use "" for brand_model and 0 for purchase_year or quantity when not said.
If nothing is said, return an empty list."""

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

    suspend fun listen(audio: File): List<Mention> {
        val bytes = audio.readBytes()
        val response = model.generateContent(content {
            inlineData(bytes, "audio/mp4")
            text(LISTEN_PROMPT)
        })
        return parseMentions(response.text ?: "")
    }
}

fun parseMentions(text: String): List<Mention> {
    val t = text.trim().removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
    val arr = runCatching { JSONObject(t.substring(t.indexOf('{'), t.lastIndexOf('}') + 1)).optJSONArray("mentions") }
        .getOrNull() ?: return emptyList()
    return (0 until arr.length()).mapNotNull { i ->
        val o = arr.optJSONObject(i) ?: return@mapNotNull null
        val said = o.optString("text").trim()
        val kind = o.optString("kind").lowercase().let { if (it == "room") "room" else "item" }
        if (said.isEmpty()) null
        else Mention(
            atMs = (o.optDouble("t", 0.0) * 1000).toLong().coerceAtLeast(0),
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
