// Top-level build file where you can add configuration options common to all sub-projects/modules.
plugins {
    alias(libs.plugins.android.application) apply false
    // Puts Kotlin 2.4 on the build path so AGP's built-in Kotlin compiles with it
    // (Firebase AI Logic is built with Kotlin 2.4). Not applied to any module.
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.google.services) apply false
}