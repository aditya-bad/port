package com.livedeploy.app

import android.app.Application
import com.livedeploy.app.data.SettingsStore

class LiveDeployApplication : Application() {
    lateinit var settingsStore: SettingsStore
        private set

    override fun onCreate() {
        super.onCreate()
        settingsStore = SettingsStore(applicationContext)
    }
}
