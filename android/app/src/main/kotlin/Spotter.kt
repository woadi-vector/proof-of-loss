@file:OptIn(com.google.firebase.ai.type.PublicPreviewAPI::class)

package com.woadivector.proofofloss

import android.graphics.Bitmap
import com.google.firebase.Firebase
import com.google.firebase.ai.DownloadStatus
import com.google.firebase.ai.InferenceMode
import com.google.firebase.ai.OnDeviceConfig
import com.google.firebase.ai.OnDeviceModelStatus
import com.google.firebase.ai.ai
import com.google.firebase.ai.type.GenerativeBackend
import com.google.firebase.ai.type.PublicPreviewAPI
import com.google.firebase.ai.type.Schema
import com.google.firebase.ai.type.content
import com.google.firebase.ai.type.generationConfig
import kotlinx.coroutines.flow.Flow
import org.json.JSONArray
import org.json.JSONObject

/** Cloud model used when Gemini Nano is not available on the phone. */
const val CLOUD_MODEL = "gemini-3.5-flash-lite"

enum class Mode(val label: String, val inference: InferenceMode) {
    PREFER_ON_DEVICE("On device first", InferenceMode.PREFER_ON_DEVICE),
    ONLY_ON_DEVICE("On device only", InferenceMode.ONLY_ON_DEVICE),
    ONLY_IN_CLOUD("Cloud only", InferenceMode.ONLY_IN_CLOUD),
}

data class SpotResult(val items: List<Spotted>, val source: String, val ms: Long, val raw: String)

private const val SPOT_PROMPT = """You are helping a field insurance adjuster record what is in a room.
List up to 6 household items, the most valuable first, (furniture, electronics, appliances, clothing, decor, tools) you can see in this photo.
For each item give:
- name: a short plain name, like "65 inch television" or "leather recliner"
- brand_model: the brand and model only if you can read them on the item; otherwise ""
- clear: true only if the photo shows the item well enough for someone to identify it later
Do not guess brands. Do not estimate value, age or damage. Do not list walls, floors, windows or people.
Answer only with JSON: {"items":[{"name":"...","brand_model":"...","clear":true}]}"""

/**
 * The one question the phone asks a model: what household items does this photo show?
 * Firebase AI Logic hybrid inference runs it on Gemini Nano through AICore when the phone has
 * the model, and on cloud Gemini otherwise. Every answer records where it actually ran.
 */
@OptIn(PublicPreviewAPI::class)
class Spotter(val mode: Mode) {

    private val schema = Schema.obj(
        properties = mapOf(
            "items" to Schema.array(
                items = Schema.obj(
                    properties = mapOf(
                        "name" to Schema.string(),
                        "brand_model" to Schema.string(),
                        "clear" to Schema.boolean(),
                    )
                )
            )
        )
    )

    private val model = Firebase.ai(backend = GenerativeBackend.googleAI()).generativeModel(
        modelName = CLOUD_MODEL,
        generationConfig = generationConfig {
            responseMimeType = "application/json"
            responseSchema = schema
        },
        onDeviceConfig = OnDeviceConfig(mode = mode.inference, maxOutputTokens = 256, temperature = 0.1f),
    )

    suspend fun status(): OnDeviceModelStatus? = model.onDeviceExtension?.checkStatus()

    fun download(): Flow<DownloadStatus>? = model.onDeviceExtension?.download()

    suspend fun warmUp() {
        runCatching { model.onDeviceExtension?.warmUp() }
    }

    suspend fun spot(photo: Bitmap): SpotResult {
        val t0 = System.currentTimeMillis()
        val response = model.generateContent(content {
            image(photo)
            text(SPOT_PROMPT)
        })
        val ms = System.currentTimeMillis() - t0
        val source = if (response.inferenceSource.toString().contains("DEVICE", ignoreCase = true))
            "on_device" else "in_cloud"
        val text = response.text ?: ""
        return SpotResult(parseItems(text), source, ms, text.take(400))
    }
}

/**
 * Lenient reading of the model's JSON. Anything malformed is dropped, never invented. When the
 * answer was cut off at the output limit, every item that was written out in full is kept.
 */
fun parseItems(text: String): List<Spotted> {
    val whole = parseWhole(text)
    if (whole.isNotEmpty()) return whole
    return Regex("""\{[^{}]*\}""").findAll(text).mapNotNull { m ->
        val o = runCatching { JSONObject(m.value) }.getOrNull() ?: return@mapNotNull null
        val name = (o.optString("name").ifBlank { o.optString("item") }).trim()
        if (name.isEmpty()) null else Spotted(name, o.optString("brand_model").trim(), o.optBoolean("clear", true))
    }.toList()
}

private fun parseWhole(text: String): List<Spotted> {
    val t = text.trim().removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
    val arr: JSONArray = runCatching {
        val a = t.indexOf('[')
        val o = t.indexOf('{')
        if (a >= 0 && (o < 0 || a < o)) JSONArray(t.substring(a, t.lastIndexOf(']') + 1))
        else {
            val obj = JSONObject(t.substring(o, t.lastIndexOf('}') + 1))
            obj.optJSONArray("items") ?: obj.optJSONArray("objects")
                ?: obj.keys().asSequence().mapNotNull { obj.optJSONArray(it) }.firstOrNull()
                ?: JSONArray().put(obj)
        }
    }.getOrNull() ?: return emptyList()
    return (0 until arr.length()).mapNotNull { i ->
        val v = arr.opt(i)
        when (v) {
            is String -> v.trim().takeIf { it.isNotEmpty() }?.let { Spotted(it, "", true) }
            is JSONObject -> {
                val name = (v.optString("name").ifBlank { v.optString("item") }).trim()
                if (name.isEmpty()) null
                else Spotted(name, v.optString("brand_model").trim(), v.optBoolean("clear", true))
            }
            else -> null
        }
    }
}
