package com.woadivector.proofofloss

/**
 * Continuity (the Story Supervisor, in Dailies terms): plain code, no model calls.
 *
 * The same exercise bike seen in six frames and two photos is one belonging, not eight.
 * Within a room, items whose names share their main word are rolled up into one line that
 * keeps every sighting as evidence. Nothing is dropped. Brand readings that disagree are kept
 * side by side with their counts; the adjuster settles them, code never picks one.
 */
data class Sighting2(val captureSha: String, val atMs: Long?, val clear: Boolean, val brand: String)

data class InventoryLine(
    val room: String,
    val name: String,
    val sightings: List<Sighting2>,
    val brandReadings: Map<String, Int>,
    val said: List<String> = emptyList(), // the adjuster's own words for this item, with brand or year
    // Clothing seen only in frames that also show a TV or monitor may be what the screen is
    // showing. Kept on the list and flagged; the adjuster decides.
    val maybeOnScreen: Boolean = false,
    val saidBrandModel: String = "", // the brand or model the adjuster said for this item, if any
) {
    val clearCount get() = sightings.count { it.clear }
    val bestSha get() = (sightings.firstOrNull { it.clear } ?: sightings.first()).captureSha
}

/** Words that name the same kind of thing. Kept short on purpose: only plain equivalents. */
private val SAME_THING = mapOf("tv" to "television", "tvs" to "television", "televisions" to "television",
    "couch" to "sofa", "couches" to "sofa", "fridge" to "refrigerator")

private val CLOTHING = setOf("jacket", "jackets", "coat", "coats", "shirt", "shirts", "pants", "jeans", "shoe", "shoes",
    "boot", "boots", "belt", "belts", "dress", "hat", "hats", "cap", "hoodie", "sweater", "skirt", "shorts", "socks",
    "gloves", "clothing", "clothes")

private fun isClothing(name: String) =
    withoutBrackets(name).lowercase().replace(Regex("[^a-z ]"), " ").trim().split(Regex("\\s+")).lastOrNull() in CLOTHING ||
        name.lowercase().startsWith("clothing")
private val SCREENS = setOf("television", "monitor", "screen")

private val GENERIC = setOf("box", "unit", "item", "thing", "object", "frame", "set", "piece", "equipment")

/** The word a name is about: the last meaningful word, singular ("storage boxes" -> "box"). */
fun headWord(name: String): String {
    val words = withoutBrackets(name).lowercase().replace(Regex("[^a-z ]"), " ").trim().split(Regex("\\s+"))
        .filter { it.length > 1 }.map { SAME_THING[it] ?: it }
    if (words.isEmpty()) return name.lowercase()
    val singular = words.map { if (it.length > 3 && it.endsWith("es") && !it.endsWith("ses")) it.dropLast(2)
        else if (it.length > 3 && it.endsWith("s")) it.dropLast(1) else it }
    // "exercise equipment frame" -> keep two words when the last one is too generic to stand alone.
    val last = singular.last()
    // "television screen" is the television, not a separate screen.
    if (last == "screen" && singular.dropLast(1).any { it == "television" || it == "monitor" }) return singular[singular.size - 2]
    return if (last in GENERIC && singular.size >= 2) singular.takeLast(2).joinToString(" ") else last
}

private fun clock(ms: Long) = "%d:%02d".format(ms / 60000, (ms / 1000) % 60)

private fun normBrand(b: String) = b.uppercase().replace(Regex("[^A-Z0-9]"), "")

fun buildInventory(captures: List<Capture>): List<InventoryLine> {
    data class Key(val room: String, val head: String)
    val groups = LinkedHashMap<Key, MutableList<Pair<Spotted, Capture>>>()
    val saidBy = LinkedHashMap<Key, MutableList<String>>()
    val saidBrand = LinkedHashMap<Key, String>()
    captures.sortedWith(compareBy({ it.fromVideo == null }, { it.atMs ?: 0L })).forEach { c ->
        c.items.filterNot { isStructure(it.name) || isNotProperty(it.name) }.forEach { it -> groups.getOrPut(Key(c.room.lowercase(), headWord(it.name))) { mutableListOf() } += it to c }
        c.said.forEach { s ->
            val k = Key(c.room.lowercase(), headWord(s.name))
            if (s.brandModel.isNotBlank() && k !in saidBrand) saidBrand[k] = s.brandModel.split(",").first().trim()
            // Each mention keeps its moment, so two monitors named at 0:05 and 0:33 stay two mentions.
            saidBy.getOrPut(k) { mutableListOf() } += s.name +
                (if (s.brandModel.isNotBlank()) " (${s.brandModel})" else "") +
                (c.atMs?.let { " at " + clock(it) } ?: "")
            // A named item the camera did not list still gets a line: the frame at that moment is its evidence.
            if (k !in groups) groups[k] = mutableListOf(Spotted(s.name, "", true) to c)
        }
    }
    return groups.map { (key, list) ->
        // The most common full name stands for the line; the others stay visible as sightings.
        val name = list.groupingBy { it.first.name.lowercase() }.eachCount().maxByOrNull { it.value }!!.key
        val brands = list.map { cleanBrand(it.first.brandModel) }.filter { it.isNotBlank() }
            .groupBy { normBrand(it) }.map { (_, v) -> v.first() to v.size }.sortedByDescending { it.second }.toMap()
        InventoryLine(
            room = list.first().second.room,
            name = name,
            sightings = list.map { (s, c) -> Sighting2(c.sha256, c.atMs, s.clear, s.brandModel) }.distinctBy { it.captureSha },
            brandReadings = brands,
            said = saidBy[key] ?: emptyList(),
            saidBrandModel = saidBrand[key] ?: "",
            maybeOnScreen = list.all { isClothing(it.first.name) } && saidBy[key].isNullOrEmpty() && list.all { (_, c) ->
                c.items.any { headWord(it.name) in SCREENS } },
        )
    }.sortedWith(compareBy({ it.room }, { -it.sightings.size }))
}
