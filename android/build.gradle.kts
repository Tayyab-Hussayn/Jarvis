// Root build: plugin versions only (gradle/libs.versions.toml). AGP 9 has Kotlin built in, so there is no kotlin-android plugin.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.compose.compiler) apply false
}
