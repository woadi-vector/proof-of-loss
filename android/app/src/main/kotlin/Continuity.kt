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
) {
    val clearCount get() = sightings.count { it.clear }
    val bestSha get() = (sightings.firstOrNull { it.clear } ?: sightings.first()).captureSha
}

private val GENERIC = setOf("box", "unit", "item", "thing", "object", "frame", "set", "piece", "equipment")

/** The word a name is about: the last meaningful word, singular ("storage boxes" -> "box"). */
fun headWord(name: String): String {
    val words = name.lowercase().replace(Regex("[^a-z ]"), " ").trim().split(Regex("\\s+")).filter { it.length > 1 }
    if (words.isEmpty()) return name.lowercase()
    val singular = words.map { if (it.length > 3 && it.endsWith("es") && !it.endsWith("ses")) it.dropLast(2)
        else if (it.length > 3 && it.endsWith("s")) it.dropLast(1) else it }
    // "exercise equipment frame" -> keep two words when the last one is too generic to stand alone.
    val last = singular.last()
    return if (last in GENERIC && singular.size >= 2) singular.takeLast(2).joinToString(" ") else last
}

private fun normBrand(b: String) = b.uppercase().replace(Regex("[^A-Z0-9]"), "")

fun buildInventory(captures: List<Capture>): List<InventoryLine> {
    data class Key(val room: String, val head: String)
    val groups = LinkedHashMap<Key, MutableList<Pair<Spotted, Capture>>>()
    val saidBy = LinkedHashMap<Key, MutableList<String>>()
    captures.sortedWith(compareBy({ it.fromVideo == null }, { it.atMs ?: 0L })).forEach { c ->
        c.items.forEach { it -> groups.getOrPut(Key(c.room.lowercase(), headWord(it.name))) { mutableListOf() } += it to c }
        c.said.forEach { s ->
            val k = Key(c.room.lowercase(), headWord(s.name))
            saidBy.getOrPut(k) { mutableListOf() } += s.name + if (s.brandModel.isNotBlank()) " (${s.brandModel})" else ""
            // A named item the camera did not list still gets a line: the frame at that moment is its evidence.
            if (k !in groups) groups[k] = mutableListOf(Spotted(s.name, "", true) to c)
        }
    }
    return groups.map { (key, list) ->
        // The most common full name stands for the line; the others stay visible as sightings.
        val name = list.groupingBy { it.first.name.lowercase() }.eachCount().maxByOrNull { it.value }!!.key
        val brands = list.map { it.first.brandModel }.filter { it.isNotBlank() }
            .groupBy { normBrand(it) }.map { (_, v) -> v.first() to v.size }.sortedByDescending { it.second }.toMap()
        InventoryLine(
            room = list.first().second.room,
            name = name,
            sightings = list.map { (s, c) -> Sighting2(c.sha256, c.atMs, s.clear, s.brandModel) }.distinctBy { it.captureSha },
            brandReadings = brands,
            said = saidBy[key] ?: emptyList(),
        )
    }.sortedWith(compareBy({ it.room }, { -it.sightings.size }))
}
