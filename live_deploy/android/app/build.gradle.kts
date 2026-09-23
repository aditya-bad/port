plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.livedeploy.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.livedeploy.app"
        minSdk = 26   // Glance (the widget toolkit) requires 26+; also lets us
                       // skip a lot of pre-Compose/pre-notification-channel
                       // back-compat code for a personal-use app with no
                       // real minSdk pressure.
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.03")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.activity:activity-compose:1.9.2")

    // Compose UI
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // Navigation
    implementation("androidx.navigation:navigation-compose:2.8.1")

    // Networking — Retrofit + OkHttp + kotlinx.serialization (matches the
    // backend's own plain-JSON style; avoids pulling in Gson/Moshi too for
    // one extra converter).
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-kotlinx-serialization:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")

    // Settings storage (server URL + API key) — Preferences DataStore, not
    // EncryptedSharedPreferences: the security library's Play-Services-based
    // MasterKey has a long history of device-specific initialization
    // failures, and the actual secret here (the API key) is exactly as
    // sensitive as a bearer token any other app on a rooted/compromised
    // device could extract regardless — DataStore's own private-by-default
    // app storage is the same practical protection level as the web app's
    // own plaintext config.json already relies on server-side.
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    // Widget (Jepack Glance — Compose-shaped API for App Widgets, not the
    // legacy RemoteViews XML approach)
    implementation("androidx.glance:glance-appwidget:1.1.1")
    implementation("androidx.glance:glance-material3:1.1.1")

    // Periodic widget refresh
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
