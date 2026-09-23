// live_deploy Android client — root build file.
//
// Plugin versions are declared here (with apply false) and applied per
// module — the standard Gradle "plugins {} block, version catalog-free"
// layout. Kept deliberately simple (no version catalog / libs.versions.toml)
// since this is a small, single-module app; revisit if a second module
// (e.g. a shared network layer split out for a future wearOS/TV target)
// ever gets added.
plugins {
    id("com.android.application") version "8.6.1" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
}
